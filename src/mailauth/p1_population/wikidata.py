"""Wikidata から企業リストを再構築する（DESIGN.md P1 / Sprint 1.5）。

**fortune.com はスクレイピングも二次利用も規約で禁止**されている。会社名の
リストは CC0 のソースから作り直す。Fortune の編集著作物である順位と売上高は
取得もしないし成果物にも含めない（`constraints.exclude_fields`）。

Wikidata Query Service の作法
  - User-Agent は必須。連絡先を含める（未設定だと 403 で弾かれる）
  - POST で投げる。SPARQL は長くなり GET の URL 長制限に当たる
  - タイムアウトは60秒。重いクエリは 500 で返る
  - 失敗したら黙って空を返さない。**「取れなかった」を上に伝える**
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from ..normalize import domain_from_url
from ..paths import config_path

ENDPOINT = "https://query.wikidata.org/sparql"
#: 連絡先を**含めていなかった。**
#:
#: この行のすぐ上のコメントは前から「連絡先を含めるのが作法」と書いて
#: いたのに、実際に送っていた UA には連絡先が無かった。robot policy が
#: 求めているので入れる。
#:
#: **ただし 403 の原因はこれではなかった。** 最初そう決めつけて、直して
#: から試したらまだ 403 だった。切り分けた結果は下の `run_query` に書く。
BASE_USER_AGENT = "mailauth-observatory/0.1 (https://github.com/suminot/mailauth-observatory"
TIMEOUT_SEC = 60.0


def user_agent() -> str:
    """WDQS に名乗る文字列。**連絡先があれば必ず入れる。**

    Wikimedia の robot policy は連絡先を求めている。`MAILAUTH_CONTACT_EMAIL`
    は SEC EDGAR でも使う同じ鍵なので、新しい設定項目は増やさない。
    """
    from ..config import credential

    contact = credential("MAILAUTH_CONTACT_EMAIL")
    return f"{BASE_USER_AGENT}; {contact})" if contact else f"{BASE_USER_AGENT})"


class WikidataError(RuntimeError):
    """取得に失敗した。**空リストを返して「0社だった」と誤解させない。**"""


@dataclass
class ForeignRow:
    """Wikidata から得た1社分。EDINET の行と同じ役割。

    国内側と違い、identity の主キーは LEI である。SEC EDGAR は米国提出者
    しかカバーしないので、Global 500 のように多国籍の母集団では CIK を
    主キーにできない（DESIGN.md configs/populations/global500.yaml）。
    """

    qid: str
    name: str
    name_en: str | None = None
    lei: str | None = None
    cik: str | None = None
    website: str | None = None
    ticker: str | None = None
    country: str | None = None
    #: SIC は SEC EDGAR から後で付ける
    sic: str | None = None
    sic_label: str | None = None
    notes: list[str] = field(default_factory=list)


def load_query(path: str | Path) -> str:
    """SPARQL ファイルを読む。`#+` で始まる行は運用メモなので落とす。"""
    p = config_path(str(path))
    if not p.is_file():
        raise WikidataError(f"SPARQL が見つかりません: {p}")
    lines = [
        ln
        for ln in p.read_text(encoding="utf-8").splitlines()
        if not ln.lstrip().startswith("#+")
    ]
    query = "\n".join(lines).strip()
    if not query:
        raise WikidataError(f"SPARQL が空です: {p}")
    return query


def run_query(query: str, *, client: httpx.Client | None = None) -> list[dict[str, Any]]:
    """SPARQL を実行して bindings をそのまま返す。

    **HTTP/2 で話す。** WDQS は HTTP/1.1 の問い合わせを robot policy で
    弾く。httpx の既定は HTTP/1.1 なので、**開発環境から 403 が返り続け、
    米国 P1 の official_url 欠損が 100% になっていた**（2026-09-30）。

    同じ環境・同じ URL・同じ UA で切り分けた実測:

        curl（既定で HTTP/2）           200
        httpx 既定（HTTP/1.1）          403
        httpx + Accept-Encoding 変更    403
        httpx + http2=True              200

    UA に連絡先を入れるのは robot policy への作法であって、**403 の原因
    ではなかった。** 最初そう決めつけて直し、まだ 403 だったので測り直した。
    """
    owned = client is None
    c = client or httpx.Client(timeout=TIMEOUT_SEC, follow_redirects=True, http2=True)
    try:
        resp = c.post(
            ENDPOINT,
            data={"query": query},
            headers={
                "User-Agent": user_agent(),
                "Accept": "application/sparql-results+json",
            },
        )
        if resp.status_code != 200:
            raise WikidataError(
                f"Wikidata が {resp.status_code} を返した: {resp.text[:200]}"
            )
        payload = resp.json()
    except httpx.HTTPError as exc:
        raise WikidataError(f"Wikidata に接続できない: {exc}") from exc
    finally:
        if owned:
            c.close()

    return list((payload.get("results") or {}).get("bindings") or [])


def _value(binding: dict, key: str) -> str | None:
    cell = binding.get(key)
    if not cell:
        return None
    value = str(cell.get("value") or "").strip()
    return value or None


def _qid(uri: str | None) -> str | None:
    if not uri:
        return None
    return uri.rstrip("/").rsplit("/", 1)[-1]


def parse_bindings(bindings: list[dict[str, Any]]) -> tuple[list[ForeignRow], dict]:
    """bindings を1社1行にまとめる。

    SPARQL の結果は OPTIONAL の組み合わせで同じ企業が複数行に散る。
    qid でまとめ、値が競合したら**最初のものを採って注記を残す**。
    黙って上書きすると、どちらが採用されたか分からなくなる。
    """
    by_qid: dict[str, ForeignRow] = {}
    stats = {"bindings": len(bindings), "conflicts": 0, "no_qid": 0, "no_name": 0}

    for binding in bindings:
        qid = _qid(_value(binding, "company"))
        if not qid:
            stats["no_qid"] += 1
            continue
        name = _value(binding, "companyLabel")
        if not name:
            stats["no_name"] += 1
            continue

        row = by_qid.get(qid)
        if row is None:
            row = ForeignRow(qid=qid, name=name)
            by_qid[qid] = row

        for field_name, key in (
            ("name_en", "companyLabelEn"),
            ("lei", "lei"),
            ("cik", "cik"),
            ("website", "website"),
            ("ticker", "tickerLabel"),
        ):
            value = _value(binding, key)
            if value is None:
                continue
            current = getattr(row, field_name)
            if current is None:
                setattr(row, field_name, value)
            elif current != value:
                stats["conflicts"] += 1
                if len(row.notes) < 5:
                    row.notes.append(f"{field_name} が競合: {current} / {value}")

        country = _qid(_value(binding, "country"))
        if country and row.country is None:
            row.country = country

    return sorted(by_qid.values(), key=lambda r: r.qid), stats


def fetch(query_path: str | Path, *, client: httpx.Client | None = None):
    """SPARQL を読んで実行し、行にして返す。"""
    query = load_query(query_path)
    return parse_bindings(run_query(query, client=client))


def _ticker_key(raw: str) -> str | None:
    """ティッカー。大文字に揃え、英数と `.` `-` だけを通す。

    **弱い鍵である。** CIK や法人番号と違って一意性が保証されていないので、
    これで突合するときは衝突を捨てること（`drop_on_conflict`）。
    """
    s = "".join(c for c in raw.strip().upper() if c.isalnum() or c in ".-")
    return s or None


def _securities_code_key(raw: str) -> str | None:
    """国内の証券コード。4桁の数字だけを通す。

    **桁数が違うものは捨てる。** Wikidata の P249 には海外市場の
    ティッカーも入るので、4桁でないものを通すと別の会社に化ける。
    """
    digits = "".join(c for c in raw if c.isdigit())
    return digits if len(digits) == 4 else None


#: 社名の比較で落とす語。法人格と記号だけを落とし、**中身の語は残す**
_NAME_NOISE = (
    "株式会社", "有限会社", "合同会社", "ホールディングス",
    "corporation", "incorporated", "company", "limited", "holdings",
    "corp", "inc", "ltd", "llc", "plc", "co", "group", "the",
)


def _name_tokens(name: str) -> set[str]:
    """語の集合。**並びを見ない比較**に使う。"""
    return set(_ordered_tokens(name))


def names_agree(ours: str | None, theirs: str | None) -> bool:
    """同じ会社の名前と見てよいか。**弱い鍵で突合するときの歯止め。**

    ティッカーは一意ではない。使い回しや上場廃止後の再割当てがあるので、
    **鍵が当たっただけでは別の会社のサイトを紐づけうる。**
    「突合できない」は取り逃がしで済むが、誤った突合は**その企業の
    データとして別の会社の測定値を出す**ので、桁違いに悪い。

    片方が空なら判断できないので False を返す（通さない）。
    語が完全に含まれている方向があれば同じと見る ── 表記ゆれ
    （`Alphabet Inc.` と `Alphabet`）を通し、別会社は落とす。
    """
    if not ours or not theirs:
        return False
    a, b = _name_tokens(ours), _name_tokens(theirs)
    if not a or not b:
        return False
    if a <= b or b <= a:
        return True
    # **日本語には語の切れ目が無い。**
    #
    # 「コカ・コーラ　ボトラーズジャパンホールディングス株式会社」と
    # 「コカ・コーラボトラーズジャパンホールディングス」は同じ会社だが、
    # 片方にだけ全角スペースが入っているので語の集合では一致しない。
    #
    # **含有ではなく一致で通す。** 含有にすると「日本電気」が
    # 「日本電気硝子」に含まれてしまい、別会社を同じ会社として扱う ──
    # この経路で一番まずい間違いである。
    return _joined(ours) == _joined(theirs)


def _joined(name: str) -> str:
    """空白を落として1つながりにしたもの。語の切れ目が無い言語向け。"""
    return "".join(sorted_tokens) if (sorted_tokens := _ordered_tokens(name)) else ""


def _ordered_tokens(name: str) -> list[str]:
    """社名を比較できる語に割る。**正規化はここ1か所だけ。**

    最初 `_name_tokens` と2か所に同じ処理を書いていて、片方の正規化を
    外しても**もう片方が拾うので検査が素通りした。** 1つにまとめてある。

    **全角と半角を揃える。** EDINET は「ＤＯＷＡホールディングス」、
    Wikidata は「DOWAホールディングス」と書く。揃えないと**同じ会社が
    別会社として落ちる** ── 2026-09-30 の実測で、社名で捨てた国内 229 社の
    うち目に見える範囲はほとんどこれだった（ＩＮＰＥＸ / ｆａｎｔａｓｉｓｔａ /
    ｍｅｉｔｏ、全角スペース入りの社名も）。
    """
    s = unicodedata.normalize("NFKC", name).lower()
    for ch in ".,&\'\"()-/":
        s = s.replace(ch, " ")
    for noise in _NAME_NOISE:
        s = s.replace(noise.lower(), " ")
    return [w for w in s.split() if w]


#: 1社に複数あって当たり前の欄。**競合として数えない。**
#:
#: 国内のクエリは日本語か英語のラベルを要求している（語順・表記が
#: 名簿と揃わないため両方欲しい）。ところが `drop_on_conflict` が
#: 「同じ鍵に違う値」を一律に競合として数えていたので、**ラベルが2つ
#: ある会社が丸ごと捨てられていた** ── 2026-09-30 の実測で、国内
#: 3,817社のうち 1,828 件がこれで落ちていた。
#:
#: 競合として見るのは、**どの会社かを決める欄**（公式サイト・LEI）だけ。
#: ラベルは決める側ではなく、突き合わせて確かめる側である。
MULTI_VALUED_FIELDS = frozenset({"label"})

#: 複数値を1つの文字列にまとめるときの区切り。ラベルには現れない
MULTI_VALUE_SEPARATOR = "\n"


def multi_values(value: str | None) -> list[str]:
    """`MULTI_VALUED_FIELDS` の欄を元の並びに戻す。"""
    if not value:
        return []
    return [v for v in value.split(MULTI_VALUE_SEPARATOR) if v]


def _conflict_key(field: str, value: str) -> str:
    """競合かどうかを**使う形で**比べる。

    公式サイトは URL のまま比べていたので、同じ会社の日本語版と英語版、
    `http` と `https`、末尾スラッシュの有無が「どちらの会社か分からない」
    として扱われ、**その会社ごと捨てられていた。**

    実測（2026-09-30、国内 3,817社）:

        ホクト      https://www.hokto-kinoko.co.jp/
                    https://www.hokto-kinoko.co.jp/lang/en/
        西松建設    https://www.nishimatsu.co.jp
                    https://www.nishimatsu.co.jp/

    使うのはドメインなので、ドメインで比べる。**別の会社なら別の
    ドメインになる**ので、歯止めとしての働きは変わらない。
    """
    if field == "website":
        return domain_from_url(value) or value.strip().lower()
    return value


def _cik_key(raw: str) -> str | None:
    """Wikidata の CIK は桁揃えがまちまち。SEC に合わせて10桁に揃える。"""
    try:
        return f"{int(raw):010d}"
    except ValueError:
        return None


def _houjin_bangou_key(raw: str) -> str | None:
    """法人番号は13桁。**桁数が違うものは捨てる。**

    Wikidata の値は利用者が入れたもので、ハイフン入りや桁落ちが混じる。
    正規化して通すと、別の会社の番号に化ける危険があるので**捨てる方を選ぶ**
    （突合できないのは「取れなかった」であって、誤った突合より安全）。
    """
    digits = "".join(c for c in raw if c.isdigit())
    return digits if len(digits) == 13 else None


#: 一次名簿ごとの突合鍵。**どの列で突き合わせるかは名簿によって違う。**
IDENTITY_KEYS: dict[str, Callable[[str], str | None]] = {
    "cik": _cik_key,
    "houjin_bangou": _houjin_bangou_key,
    # **弱い鍵。** 一意性が保証されていないので、突合するときは
    # `drop_on_conflict=True` と `names_agree` を併せて使う
    "ticker": _ticker_key,
    "securities_code": _securities_code_key,
}


def fetch_identity(
    query_path: str | Path,
    *,
    key: str = "cik",
    fields: tuple[str, ...] = ("website", "lei"),
    client: httpx.Client | None = None,
    drop_on_conflict: bool = False,
) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
    """突合鍵 -> {website, lei, …} を**1クエリで**引く。

    **1社ずつ引かない。** 数千社を個別に照会すると WDQS に不当な負荷を
    かける。全件を1回で取り、手元で突合する。

    同じ鍵に複数の値があったら**最初のものを採って競合を数える。**
    黙って上書きすると、どちらが採用されたか分からなくなる。

    **弱い鍵では `drop_on_conflict=True` にする。** CIK や法人番号は
    一意なので競合は向こうのデータ誤りだが、ティッカーは一意ではない
    ── 競合はそのまま「どちらの会社か分からない」を意味する。
    最初のものを採ると**別の会社のサイトを紐づける。**

    鍵は名簿によって違う（米国は CIK、国内は法人番号）。正規化のしかたも
    違うので `IDENTITY_KEYS` に分けてある。
    """
    normalize = IDENTITY_KEYS.get(key)
    if normalize is None:
        raise WikidataError(f"突合鍵 {key!r} の正規化が定義されていません")

    bindings = run_query(load_query(query_path), client=client)
    out: dict[str, dict[str, str]] = {}
    stats: dict[str, Any] = {
        "bindings": len(bindings),
        "key": key,
        "key_count": 0,
        "unusable_keys": 0,
        "conflicts": 0,
        # 弱い鍵で、どちらの会社か決められずに捨てた数
        "dropped_ambiguous": 0,
    }
    for field_name in fields:
        stats[field_name] = 0
    ambiguous: set[str] = set()

    for binding in bindings:
        raw = _value(binding, key)
        if not raw:
            continue
        normalized = normalize(raw)
        if normalized is None:
            # **黙って飛ばさない。** 突合できなかった数が見えないと、
            # 被覆率が低い原因が「無い」のか「読めない」のか分からない
            stats["unusable_keys"] += 1
            continue
        entry = out.setdefault(normalized, {})
        for field_name in fields:
            value = _value(binding, field_name)
            if not value:
                continue
            if field_name in MULTI_VALUED_FIELDS:
                # **複数あって当たり前の欄。** 積んでいくだけで競合にしない
                existing = multi_values(entry.get(field_name))
                if value not in existing:
                    existing.append(value)
                    entry[field_name] = MULTI_VALUE_SEPARATOR.join(existing)
                    stats[field_name] += 1
                continue
            if field_name not in entry:
                entry[field_name] = value
                stats[field_name] += 1
            elif _conflict_key(field_name, entry[field_name]) != _conflict_key(
                field_name, value
            ):
                stats["conflicts"] += 1
                if drop_on_conflict:
                    # **どちらか分からないなら、両方使わない。**
                    # 取り逃がしは取り逃がしで済むが、誤った突合は
                    # 別の会社の測定値をその企業のものとして出す
                    ambiguous.add(normalized)

    for bad in ambiguous:
        out.pop(bad, None)
    stats["dropped_ambiguous"] = len(ambiguous)
    stats["key_count"] = len(out)
    return out, stats


def fetch_cik_identity(
    query_path: str | Path, *, client: httpx.Client | None = None
) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
    """CIK -> {website, lei}。`fetch_identity` の米国向けの呼び出し。"""
    identity, stats = fetch_identity(query_path, key="cik", client=client)
    # 既存の呼び出しと実行記録が読む名前を残す
    stats["cik_count"] = stats["key_count"]
    return identity, stats
