"""identity の決定と母集団間の重複排除（DESIGN.md P1 / Sprint 1.5）。

日本企業は jp-prime と global500 の両方に、米国企業は us-fortune500 と
global500 の両方に現れる。**同じ会社が2つの entity になると、企業ベースの
採用率が二重に数えられる。**

主キーは LEI である。全世界を1つのキーで揃えられるのはこれだけ。
ただし LEI は全社にあるわけではないので、欠損時は cik / houjin_bangou /
正規化した商号の順にフォールバックする。

**どのキーで同定したかを必ず記録する。** 後から「なぜこの2社が同一と
判定されたか」を追えないと、誤マージに気付けない。商号一致は最も弱い
根拠なので、それで結合したものは manifest に件数を出す。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..contracts import Entity
from ..normalize import normalize_name

#: 同定に使うキーの強さ。強い順に試す
MATCH_KEYS = ("lei", "cik", "houjin_bangou", "name_normalized")

#: 商号一致は最も弱い。別会社を同一視しうるので件数を必ず出す
WEAK_MATCH_KEY = "name_normalized"


def entity_id_for(
    *,
    lei: str | None = None,
    cik: str | None = None,
    houjin_bangou: str | None = None,
    edinet_code: str | None = None,
    qid: str | None = None,
) -> tuple[str, str]:
    """entity_id と、その根拠になったキーの名前を返す。

    接頭辞で由来が分かるようにしてある。LEI が付いた企業は `lei:` になり、
    国内側の `jp:` から付け替わる。付け替えたことは `is_duplicate_of` に残す。
    """
    if lei:
        return f"lei:{lei.strip().upper()}", "lei"
    if houjin_bangou:
        return f"jp:{houjin_bangou}", "houjin_bangou"
    if cik:
        return f"cik:{str(cik).lstrip('0') or '0'}", "cik"
    if edinet_code:
        return f"edinet:{edinet_code}", "edinet_code"
    if qid:
        return f"wd:{qid}", "qid"
    raise ValueError("entity_id を決められません（同定できる識別子が1つも無い）")


@dataclass
class MergeStats:
    merged: int = 0
    by_key: dict[str, int] = field(default_factory=dict)
    #: 商号一致だけで結合したもの。誤マージの温床なので個別に数える
    weak_merges: int = 0
    weak_samples: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _has_strong_key(entity: Entity) -> bool:
    """LEI / CIK / 法人番号のいずれかを持っているか。

    どれか1つでもあれば、商号の一致だけで別の entity と結合してはいけない。
    """
    return bool(entity.lei or entity.cik or entity.houjin_bangou)


def _keys(entity: Entity) -> list[tuple[str, str]]:
    """この entity を同定できるキーの列。強い順。"""
    out: list[tuple[str, str]] = []
    if entity.lei:
        out.append(("lei", entity.lei.strip().upper()))
    if entity.cik:
        out.append(("cik", str(entity.cik).lstrip("0")))
    if entity.houjin_bangou:
        out.append(("houjin_bangou", entity.houjin_bangou))
    if entity.name_normalized:
        out.append((WEAK_MATCH_KEY, entity.name_normalized))
    return out


def merge_populations(
    existing: list[Entity], incoming: list[Entity]
) -> tuple[list[Entity], MergeStats]:
    """既存の母集団に新しい母集団を重ねる。

    同一と判定できたら `population_ids` に足すだけで、行は増やさない。
    **強いキーで一致したものだけを先に確定させる。** 商号一致を先に処理すると、
    LEI で区別できるはずの別会社を潰してしまう。
    """
    stats = MergeStats()
    merged: list[Entity] = [e.model_copy(deep=True) for e in existing]

    # 強いキーから順に索引を作る。弱いキーの索引は最後に使う
    index: dict[tuple[str, str], Entity] = {}
    for entity in merged:
        for key in _keys(entity):
            index.setdefault(key, entity)

    for candidate in incoming:
        hit: Entity | None = None
        hit_key: str | None = None
        for key_name, value in _keys(candidate):
            found = index.get((key_name, value))
            if found is None:
                continue
            if key_name == WEAK_MATCH_KEY and (
                _has_strong_key(candidate) or _has_strong_key(found)
            ):
                # **どちらかに強いキーがあるなら商号一致では結合しない。**
                # LEI が違う2社は別会社である。商号が同じだけで潰すと、
                # 区別できるはずのものを混ぜてしまう
                continue
            hit, hit_key = found, key_name
            break

        if hit is None:
            new = candidate.model_copy(deep=True)
            merged.append(new)
            for key in _keys(new):
                index.setdefault(key, new)
            continue

        stats.merged += 1
        stats.by_key[hit_key or "?"] = stats.by_key.get(hit_key or "?", 0) + 1
        if hit_key == WEAK_MATCH_KEY:
            stats.weak_merges += 1
            if len(stats.weak_samples) < 10:
                stats.weak_samples.append(f"{hit.name} == {candidate.name}")

        for population_id in candidate.population_ids:
            if population_id not in hit.population_ids:
                hit.population_ids.append(population_id)
        hit.population_ids.sort()
        _fill_missing(hit, candidate)
        # 統合後も新しいキーで引けるようにする
        for key in _keys(hit):
            index.setdefault(key, hit)

    if stats.weak_merges:
        stats.notes.append(
            f"商号の一致だけで {stats.weak_merges} 件を同一と判定した。"
            "LEI も CIK も法人番号も無い企業どうしなので、別会社を"
            "同一視している可能性がある。上位のものは目視で確認すること"
        )
    return merged, stats


#: 統合時に埋めてよい列。**上書きはしない。**
#: 先に入っている値を新しい母集団の値で塗り替えると、どちらが正なのか
#: 分からなくなる。空いているところだけを埋める
FILLABLE = (
    "name_en",
    "lei",
    "cik",
    "ticker",
    "houjin_bangou",
    "edinet_code",
    "securities_code",
    "official_url",
    "official_domain",
    "industry_scheme",
    "industry_code",
    "industry_label",
    "common12_code",
    "common12_label",
)


def _fill_missing(target: Entity, source: Entity) -> None:
    for name in FILLABLE:
        if getattr(target, name, None) in (None, "") and getattr(source, name, None):
            setattr(target, name, getattr(source, name))


def promote_to_lei(entities: list[Entity]) -> int:
    """LEI が付いた entity の ID を `lei:` に付け替える。

    付け替え前の ID は `is_duplicate_of` に残す。月次で ID が変わると
    時系列が切れるので、**どこから来た ID かを必ず辿れるようにする。**
    """
    promoted = 0
    for entity in entities:
        if not entity.lei:
            continue
        new_id, _ = entity_id_for(lei=entity.lei)
        if entity.entity_id == new_id:
            continue
        entity.is_duplicate_of = entity.entity_id
        entity.entity_id = new_id
        promoted += 1
    return promoted


def normalized(name: str) -> str:
    return normalize_name(name)
