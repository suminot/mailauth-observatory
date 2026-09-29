"""fact とフィンガープリントの照合、および二段推定（DESIGN.md P6）。

ここが原則2（事実と推察を混ぜない）の要である。どの規則がどの観測値に
一致したかを evidence として必ず残す。**根拠を辿れない推定は出さない。**

二段推定の考え方
  ゲートウェイ型製品は MX を自社に向けさせるため、MX だけでは背後の実基盤が
  見えない。だから MX が security_gateway に一致しても、SPF include や
  DKIM CNAME から導いた mail_platform を**別カテゴリとして残す**。
  単一ベンダーに丸めると「M365 の上に GUARDIANWALL」という実態が消える。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..contracts import (
    INFERENCE_PRIORITY,
    ConfidenceLevel,
    EvidenceRecordType,
    InferenceCategory,
    UndetectableReason,
)
from ..p5_parse.orgdomain import resolve_psl
from .fingerprints import (
    DEFAULT_LAYERS,
    VERIFICATION_CATEGORY,
    Rule,
    RuleSet,
)

#: 所有権確認 TXT だけが根拠のとき、その製品が「現行のメール基盤」である
#: 確証はない。何か月連続で裏付けが無ければ stale に降格するか（DESIGN.md P6）
STALE_CONSECUTIVE_MONTHS = 3

#: 確度の並び。上げ下げの計算に使う
_CONFIDENCE_ORDER = [ConfidenceLevel.LOW, ConfidenceLevel.MEDIUM, ConfidenceLevel.HIGH]

#: 「検出できなかった」を「使っていない」と読ませないための番兵。
#: API / OAuth 連携型の製品は原理的に DNS に痕跡を残さないため、
#: 非検出をそのまま 0 と数えると実態を大きく取り違える（DESIGN.md P6）
NOT_DETECTED_VENDOR = "not_detected"
UNDETECTABLE_API_MODE = UndetectableReason.API_MODE_PRODUCT

#: SPF を平坦化して include を消してしまう事業者（実測）。
#: **「基盤が無い」ではなく「基盤が読めない」。** 展開されると
#: `include:_spf.google.com` のような手掛かりが IP 列挙やマクロに化ける
SPF_FLATTENING_MARKERS = (
    "powerspf.com",
    "_spf.vali.email",
    "spf.has.pphosted.com",
    "spf25.jp",
)

#: 理由ごとの言い回し。**画面にも明細にも同じ文を出す**
UNDETECTABLE_MESSAGES: dict[str, str] = {
    UndetectableReason.API_MODE_PRODUCT: (
        "DNS 上でメールセキュリティ製品を検出できなかった。"
        "MX を変更せず API / OAuth で連携する製品は原理的に痕跡を残さないため、"
        "これは「使っていない」ことを意味しない"
    ),
    UndetectableReason.SELF_HOSTED_MX: (
        "MX が自社ドメイン配下にある。**運用はしているが、"
        "ホスト名からは製品が分からない**（自作かアプライアンスかも区別できない）。"
        "「製品を使っていない」ことを意味しない"
    ),
    UndetectableReason.SPF_FLATTENED: (
        "SPF が平坦化されていて include が読めない。**「基盤が無い」ではなく"
        "「基盤が読めない」。** 10 lookup 制限を避けるために include を IP 列挙や"
        "マクロへ展開すると、基盤を示す手掛かりが消える"
    ),
    UndetectableReason.NOT_OBSERVED: (
        "そもそも観測できていない。**「無い」ではない**（原則5）。"
        "検出できなかったことの根拠として数えてはならない"
    ),
}


def undetectable_reason_for(fact: dict) -> str:
    """検出できなかった理由を1つ選ぶ。

    **「未検出」を1つに丸めない。** 理由が違えば読み方が違う。
    強い順（言えることが多い順）に見て、最初に当たったものを返す。

      1. 観測できていない ── 何も言えない。他の判定より先に来る
      2. MX が自社ドメイン配下 ── 運用はしている。製品が分からないだけ
      3. SPF が平坦化されている ── 基盤が読めない
      4. それ以外 ── API 連携型の可能性が残る（既定）
    """
    if not fact.get("observed"):
        return UndetectableReason.NOT_OBSERVED

    hosts = [
        (h or "").strip().rstrip(".").lower() for h in (fact.get("mx_hosts") or [])
    ]
    hosts = [h for h in hosts if h]
    own = (fact.get("org_domain_psl") or "").strip().rstrip(".").lower()
    if hosts and own:
        # **1つでも外に出ていれば自社運用とは言わない。** 前段を通している
        # 構成を「自社運用」と読むと、製品の有無の話がずれる
        if all((resolve_psl(h) or h) == own for h in hosts):
            return UndetectableReason.SELF_HOSTED_MX

    if _spf_is_unreadable(fact):
        return UndetectableReason.SPF_FLATTENED

    return UndetectableReason.API_MODE_PRODUCT


def _spf_is_unreadable(fact: dict) -> bool:
    """SPF から基盤を読み取れない状態か。

    P5 が数で判定した `spf_is_flattened` に加えて、**平坦化サービスの
    痕跡そのもの**も見る。PowerSPF のように include は1つ残るが、
    その先が展開済みという形があり、数だけでは捕まらない。
    """
    if fact.get("spf_is_flattened"):
        return True
    values = [
        *(fact.get("spf_includes") or []),
        *(fact.get("spf_mechanisms") or []),
        fact.get("raw_spf") or "",
    ]
    haystack = " ".join(str(v).lower() for v in values if v)
    return any(marker in haystack for marker in SPF_FLATTENING_MARKERS)


@dataclass
class Hit:
    """1つの規則が1つの観測値に一致したこと。"""

    rule: Rule
    record_type: str
    matched_value: str

    @property
    def priority(self) -> int:
        return INFERENCE_PRIORITY.get(self.record_type, 0)


@dataclass
class InferenceDraft:
    """1ベンダー×1カテゴリの推定。parquet 行にする前の形。"""

    category: str
    vendor: str
    product: str | None
    confidence: str
    hits: list[Hit] = field(default_factory=list)
    is_stale: bool = False
    stale_streak_months: int | None = None
    notes: list[str] = field(default_factory=list)
    undetectable_reason: str | None = None
    #: OEM 元の製品。ベンダーが違っても同じ仕組みのことがある
    engine: str | None = None
    #: 経路のどこか（実基盤 / 受信前段 / 送信前段）
    layer: str | None = None
    #: その層の代表か。層を持たない推定では None
    is_layer_primary: bool | None = None

    @property
    def rule_ids(self) -> list[str]:
        return sorted({h.rule.id for h in self.hits})

    @property
    def record_types(self) -> set[str]:
        return {h.record_type for h in self.hits}

    def evidence_json(self) -> str | None:
        if not self.hits:
            return None
        items = [
            {
                "record_type": h.record_type,
                "matched_value": h.matched_value,
                "rule_id": h.rule.id,
            }
            # 強い証拠を先に並べる。人が読むときに結論の根拠が最初に来る
            for h in sorted(
                self.hits, key=lambda h: (-h.priority, h.rule.id, h.matched_value)
            )
        ]
        return json.dumps(items, ensure_ascii=False, sort_keys=True)


def _values_for(record: str, fact: dict) -> list[str]:
    """規則の record 種別に対応する観測値を fact から取り出す。"""
    if record == EvidenceRecordType.MX:
        return list(fact.get("mx_hosts") or [])
    if record == EvidenceRecordType.SPF_INCLUDE:
        return list(fact.get("spf_includes") or [])
    if record == EvidenceRecordType.SPF_MECHANISM:
        return list(fact.get("spf_mechanisms") or [])
    if record == EvidenceRecordType.DKIM_CNAME:
        return list(fact.get("dkim_cname_targets") or [])
    if record in (EvidenceRecordType.TXT, EvidenceRecordType.VERIFICATION_TXT):
        return list(fact.get("verification_txt") or [])
    if record == EvidenceRecordType.DMARC_RUA_DOMAIN:
        return list(fact.get("dmarc_rua") or [])
    return []


#: 所有権確認 TXT しか根拠が無く、ベンダーのカテゴリが辞書から決まらない場合の既定。
#: **mail_platform にしてはいけない。** Docusign や Atlassian の所有権確認 TXT を
#: 「メール基盤は Docusign」と読むのは明確な誤りである。これらは自社ドメインの
#: 差出人で通知メールを送る事業者なので、esp（配信基盤）が最も近い。
UNRESOLVED_VERIFICATION_CATEGORY = InferenceCategory.ESP


def vendor_categories(rule_set: RuleSet) -> dict[str, str]:
    """ベンダー -> 本来のカテゴリ。

    所有権確認 TXT の規則は `category: verification_txt` を持つが、
    これは Inference のカテゴリではない。同じベンダーの他の規則から
    本来のカテゴリを引く。引けない場合の既定は
    `UNRESOLVED_VERIFICATION_CATEGORY` を参照。
    """
    out: dict[str, str] = {}
    for rule in rule_set.rules:
        if rule.category == VERIFICATION_CATEGORY:
            continue
        out.setdefault(rule.vendor, rule.category)
    return out


def find_hits(fact: dict, rule_set: RuleSet) -> list[Hit]:
    """一致した規則をすべて返す。**最初の一致で打ち切らない。**

    同じ観測から複数ベンダーが立つのは正常な状態（前段ゲートウェイ＋実基盤）。
    """
    hits: list[Hit] = []
    for rule in rule_set.rules:
        values = _values_for(rule.record, fact)
        for value in values:
            if not value or not rule.matches(value):
                continue
            record_type = rule.record
            if rule.category == VERIFICATION_CATEGORY:
                # 証拠としては最も弱い種別に揃える（INFERENCE_PRIORITY=1）
                record_type = EvidenceRecordType.VERIFICATION_TXT
            hits.append(Hit(rule=rule, record_type=record_type, matched_value=value))
    return hits


def combine_confidence(hits: list[Hit]) -> tuple[str, list[str]]:
    """複数の証拠から確度を決める（DESIGN.md P6「典型的な二段パターン」）。

    - DKIM CNAME があれば high。署名基盤は最も実基盤に近い
    - 強い証拠（MX / SPF include）が2種類以上そろえば1段上げる
    - 所有権確認 TXT しか無ければ low。単独では「利用中」と断定できない
    """
    notes: list[str] = []
    declared = max(
        (_CONFIDENCE_ORDER.index(h.rule.confidence) for h in hits), default=0
    )
    types = {h.record_type for h in hits}

    if types == {EvidenceRecordType.VERIFICATION_TXT}:
        notes.append(
            "根拠が所有権確認 TXT のみ。削除されずに残りやすく、"
            "単独では利用中と断定できない"
        )
        return ConfidenceLevel.LOW, notes

    if EvidenceRecordType.DKIM_CNAME in types:
        if declared < _CONFIDENCE_ORDER.index(ConfidenceLevel.HIGH):
            notes.append("DKIM CNAME による署名基盤の裏付けがあるため high に上げた")
        return ConfidenceLevel.HIGH, notes

    strong = {t for t in types if INFERENCE_PRIORITY.get(t, 0) >= 2}
    if len(strong) >= 2:
        upgraded = min(declared + 1, len(_CONFIDENCE_ORDER) - 1)
        if upgraded > declared:
            notes.append(
                f"独立した証拠が {len(strong)} 種類そろっているため確度を1段上げた"
                f"（{', '.join(sorted(strong))}）"
            )
        return _CONFIDENCE_ORDER[upgraded], notes

    return _CONFIDENCE_ORDER[declared], notes


def is_corroborated(rule: Rule, fact: dict) -> bool | None:
    """所有権確認 TXT に対応する MX / SPF / DKIM の裏付けがあるか。

    裏付け条件が辞書に書かれていなければ None（判定していない）を返す。
    「裏付けが無い」と「判定していない」を混ぜない（原則5）。
    """
    corr = rule.corroborated_by
    if corr is None:
        return None

    if corr.mx_pattern:
        for host in fact.get("mx_hosts") or []:
            if corr.mx_pattern.search(host or ""):
                return True
    if corr.spf_includes:
        includes = {(i or "").strip().lower() for i in fact.get("spf_includes") or []}
        if includes & set(corr.spf_includes):
            return True
    if corr.dkim_cname:
        for target in fact.get("dkim_cname_targets") or []:
            if corr.dkim_cname.search(target or ""):
                return True
    return False


def build_drafts(
    fact: dict,
    rule_set: RuleSet,
    *,
    categories: dict[str, str] | None = None,
    prior_streaks: dict[tuple[str, str], int] | None = None,
) -> list[InferenceDraft]:
    """1ドメイン分の推定を組み立てる。

    `prior_streaks` は前月までの「裏付けが無かった連続月数」。
    {(category, vendor): 月数}。3か月連続で裏付けが出なければ stale に
    降格する（DESIGN.md P6）。初回実行では空になり、その場合は
    降格しないまま連続1か月目として記録する。
    """
    resolved = categories if categories is not None else vendor_categories(rule_set)
    streaks = prior_streaks or {}

    # **層も鍵に入れる。** 「IIJ が受信前段」と「IIJ が送信前段」は
    # 別の事実で、1行に潰すと受信と送信の違いが消える
    grouped: dict[tuple[str, str | None, str], list[Hit]] = {}
    guessed: set[tuple[str, str]] = set()
    for hit in find_hits(fact, rule_set):
        category = hit.rule.category
        layer = hit.rule.layer
        if category == VERIFICATION_CATEGORY:
            category = resolved.get(hit.rule.vendor)
            if category is None:
                category = UNRESOLVED_VERIFICATION_CATEGORY
                guessed.add((category, hit.rule.vendor))
            # 所有権確認 TXT の規則は層を持たない。解決したカテゴリの
            # 既定に従う（gateway は既定を持たないので None のまま）
            layer = DEFAULT_LAYERS.get(category)
        grouped.setdefault((category, layer, hit.rule.vendor), []).append(hit)

    drafts: list[InferenceDraft] = []
    for (category, layer, vendor), hits in sorted(
        grouped.items(), key=lambda kv: (kv[0][0], kv[0][1] or "", kv[0][2])
    ):
        confidence, notes = combine_confidence(hits)
        if (category, vendor) in guessed:
            notes.append(
                f"{vendor} のカテゴリを辞書から決められないため {category} と"
                "みなしている。所有権確認 TXT 以外の規則が辞書に無い"
            )

        product = _pick_product(hits)

        draft = InferenceDraft(
            category=category,
            vendor=vendor,
            product=product,
            confidence=confidence,
            hits=hits,
            notes=notes,
            undetectable_reason=_rule_undetectable_reason(hits),
            engine=_pick_engine(hits),
            layer=layer,
        )
        for hit in hits:
            if hit.rule.note:
                draft.notes.append(f"{hit.rule.id}: {hit.rule.note}")

        _apply_stale(draft, hits, fact, streaks.get((category, vendor), 0))
        drafts.append(draft)

    mark_layer_primary(drafts)
    return drafts


def mark_layer_primary(drafts: list[InferenceDraft]) -> None:
    """層ごとに代表を1つ決める（DESIGN-platform.md §6.2）。

    **同じ層に2つ立ったら、そのまま数えると合計が100%を超える。**
    証拠の強い方を代表にする。

    **落とした方は捨てない**（原則1）。`is_layer_primary=False` の行として
    残し、なぜ代表でないのかを note に書く。消してしまうと、後から
    「本当に2つあったのか、辞書が壊れていたのか」が分からなくなる。
    """
    by_layer: dict[str, list[InferenceDraft]] = {}
    for draft in drafts:
        if draft.layer:
            by_layer.setdefault(str(draft.layer), []).append(draft)

    for layer, group in by_layer.items():
        best = max(group, key=_layer_rank)
        for draft in group:
            draft.is_layer_primary = draft is best
            if draft is not best:
                draft.notes.append(
                    f"同じ層（{layer}）に {best.vendor} も立っていて、"
                    f"そちらの方が証拠が強い。**数えるのはそちら**"
                )
            elif len(group) > 1:
                others = "、".join(d.vendor for d in group if d is not best)
                draft.notes.append(
                    f"同じ層（{layer}）に {others} も立っている。"
                    "証拠の強さでこちらを代表にした"
                )


def _layer_rank(draft: InferenceDraft) -> tuple:
    """層の代表を決める順。証拠の強さ（§4）→ 確度 → 証拠の数。

    **同点の決着はここでは付けない。** `drafts` は
    (category, layer, vendor) で並べてあり、`max` は同点なら最初のものを
    返すので、**同点ならベンダー名の昇順**で決まる ── 辞書を並べ替えても
    答えは変わらない。

    最初はここに名前を入れた比較を足したが、**上の並びで既に決まって
    いるので効いていなかった**（消しても検査が通った）。効いていない
    ものを「揺れ止め」と書いて残すと、次に読む人が守ろうとしてしまう。
    """
    strongest = max((h.priority for h in draft.hits), default=0)
    return (
        strongest,
        _CONFIDENCE_ORDER.index(draft.confidence),
        len(draft.hits),
    )


def _pick_engine(hits: list[Hit]) -> str | None:
    """OEM 元の製品。**1つに決まらなければ付けない。**

    同じベンダー・同じ層に、別の仕組みを指す規則が同時に当たることは
    あるはずがないが、起きたときに片方を勝手に選ぶと嘘になる。
    """
    engines = {h.rule.engine for h in hits if h.rule.engine}
    if len(engines) == 1:
        return next(iter(engines))
    return None


def _rule_undetectable_reason(hits: list[Hit]) -> str | None:
    """辞書側が「一致しても使っているとは言えない」と宣言しているか。

    **1つでも普通の証拠が混ざっていれば、理由は付けない。** 仮 MX を
    残したまま DKIM CNAME も出ているなら、それは現に使っている証拠が
    あるということで、利用数から外す理由にならない。
    """
    reasons = {h.rule.undetectable_reason for h in hits}
    if len(reasons) == 1:
        return next(iter(reasons))
    return None


def _pick_product(hits: list[Hit]) -> str | None:
    """同じベンダーに複数の規則が当たったとき、どの product を採るか。

    **より具体的な規則を優先する。** 受け皿として広い正規表現の規則
    （例: `\\.pphosted\\.com$`）を辞書に足せるようにしておきたいが、
    それが詳細な規則（Enterprise Protection / Essentials の区別）を
    上書きしてしまうと product が粗くなる。

    優先順は 証拠の強さ → 宣言された確度 → 正規表現の長さ（具体性の代理）。
    product を持たない規則は最後に回す。
    """

    def key(hit: Hit) -> tuple:
        return (
            1 if hit.rule.product else 0,
            hit.priority,
            _CONFIDENCE_ORDER.index(hit.rule.confidence),
            len(hit.rule.pattern.pattern),
            hit.rule.id,
        )

    return max(hits, key=key).rule.product


def _apply_stale(
    draft: InferenceDraft, hits: list[Hit], fact: dict, prior_streak: int
) -> None:
    """所有権確認 TXT の「過去の痕跡」判定（DESIGN.md P6）。

    `MS=` や `google-site-verification=` は削除されずに残りやすい。
    対応する MX / SPF / DKIM の裏付けが無ければ「過去に検討・併用したが
    現行のメール基盤ではない」と判定する。ただし**即座に stale にはしない**。
    月次差分での観測が最も確実な判別手段なので、3か月連続で裏付けが
    出なかった場合に降格する。
    """
    verification_hits = [
        h for h in hits if h.record_type == EvidenceRecordType.VERIFICATION_TXT
    ]
    if not verification_hits:
        return
    if draft.record_types - {EvidenceRecordType.VERIFICATION_TXT}:
        # 他の証拠がある。所有権確認 TXT は補強材料にすぎず stale の対象外
        return

    results = [is_corroborated(h.rule, fact) for h in verification_hits]
    if any(r is True for r in results):
        draft.notes.append("所有権確認 TXT に対応する MX / SPF / DKIM の裏付けがある")
        return
    if all(r is None for r in results):
        draft.notes.append(
            "辞書に裏付け条件が書かれていないため stale の判定をしていない"
        )
        return

    streak = prior_streak + 1
    draft.stale_streak_months = streak
    if streak >= STALE_CONSECUTIVE_MONTHS:
        draft.is_stale = True
        draft.notes.append(
            f"裏付けが {streak} か月連続で確認できないため stale に降格した"
            f"（閾値 {STALE_CONSECUTIVE_MONTHS} か月）"
        )
    else:
        draft.notes.append(
            f"裏付けが確認できない（連続 {streak} か月目）。"
            f"{STALE_CONSECUTIVE_MONTHS} か月連続で stale に降格する"
        )


def undetectable_draft(rule_set: RuleSet, fact: dict | None = None) -> InferenceDraft:
    """検出できなかったことを1行として明示する（DESIGN.md P6）。

    security_gateway が一件も検出できなかったドメインについて、
    「検出されなかった＝使っていない」ではないことをデータ側に残す。
    manifest の注記だけにすると、P7 / P8 に渡った時点で消える。

    **理由は1つではない。** 2026-09 の計測では 2,884 ドメインがここに
    落ちていたが、中身は API 連携型・自社運用・SPF 平坦化・未観測が
    混ざっていた。`fact` を渡すと理由を選び分ける
    （`undetectable_reason_for`）。
    """
    reason = (
        undetectable_reason_for(fact)
        if fact is not None
        else UndetectableReason.API_MODE_PRODUCT
    )
    notes = [UNDETECTABLE_MESSAGES[reason]]
    if reason == UndetectableReason.API_MODE_PRODUCT:
        vendors = ", ".join(
            v.vendor + (f"（{v.product}）" if v.product else "")
            for v in rule_set.undetectable
        )
        if vendors:
            notes.append(f"痕跡を残さない製品の例: {vendors}")
    return InferenceDraft(
        category=InferenceCategory.SECURITY_GATEWAY,
        vendor=NOT_DETECTED_VENDOR,
        product=None,
        confidence=ConfidenceLevel.LOW,
        undetectable_reason=reason,
        notes=notes,
    )
