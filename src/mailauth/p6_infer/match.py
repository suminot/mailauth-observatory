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
)
from .fingerprints import VERIFICATION_CATEGORY, Rule, RuleSet

#: 所有権確認 TXT だけが根拠のとき、その製品が「現行のメール基盤」である
#: 確証はない。何か月連続で裏付けが無ければ stale に降格するか（DESIGN.md P6）
STALE_CONSECUTIVE_MONTHS = 3

#: 確度の並び。上げ下げの計算に使う
_CONFIDENCE_ORDER = [ConfidenceLevel.LOW, ConfidenceLevel.MEDIUM, ConfidenceLevel.HIGH]

#: 「検出できなかった」を「使っていない」と読ませないための番兵。
#: API / OAuth 連携型の製品は原理的に DNS に痕跡を残さないため、
#: 非検出をそのまま 0 と数えると実態を大きく取り違える（DESIGN.md P6）
NOT_DETECTED_VENDOR = "not_detected"
UNDETECTABLE_API_MODE = "api_mode_product"


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

    grouped: dict[tuple[str, str], list[Hit]] = {}
    guessed: set[tuple[str, str]] = set()
    for hit in find_hits(fact, rule_set):
        category = hit.rule.category
        if category == VERIFICATION_CATEGORY:
            category = resolved.get(hit.rule.vendor)
            if category is None:
                category = UNRESOLVED_VERIFICATION_CATEGORY
                guessed.add((category, hit.rule.vendor))
        grouped.setdefault((category, hit.rule.vendor), []).append(hit)

    drafts: list[InferenceDraft] = []
    for (category, vendor), hits in sorted(grouped.items()):
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
        )
        for hit in hits:
            if hit.rule.note:
                draft.notes.append(f"{hit.rule.id}: {hit.rule.note}")

        _apply_stale(draft, hits, fact, streaks.get((category, vendor), 0))
        drafts.append(draft)

    return drafts


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


def undetectable_draft(rule_set: RuleSet) -> InferenceDraft:
    """API 連携型製品の構造的盲点を1行として明示する（DESIGN.md P6）。

    Abnormal Security や Avanan のような製品は MX を変更せず
    API / OAuth で動作するため、**原理的に DNS へ痕跡を残さない**。
    security_gateway が一件も検出できなかったドメインについて、
    「検出されなかった＝使っていない」ではないことをデータ側に残す。
    manifest の注記だけにすると、P7 / P8 に渡った時点で消える。
    """
    vendors = ", ".join(
        v.vendor + (f"（{v.product}）" if v.product else "")
        for v in rule_set.undetectable
    )
    return InferenceDraft(
        category=InferenceCategory.SECURITY_GATEWAY,
        vendor=NOT_DETECTED_VENDOR,
        product=None,
        confidence=ConfidenceLevel.LOW,
        undetectable_reason=UNDETECTABLE_API_MODE,
        notes=[
            "DNS 上でメールセキュリティ製品を検出できなかった。"
            "MX を変更せず API / OAuth で連携する製品は原理的に痕跡を残さないため、"
            "これは「使っていない」ことを意味しない",
            f"痕跡を残さない製品の例: {vendors}" if vendors else "",
        ],
    )
