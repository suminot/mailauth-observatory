"""DMARC の解釈と二重計算（DESIGN.md P5「DMARC の二重計算」）。

**受信側は依然として RFC 7489 のまま**である。RFC 9990 形式でレポートを
送っている大手は United Internet のみで、全レポーターの0.6%にすぎない。
したがって RFC 7489 準拠の判定が「実際に効いている強度」に最も近い。

しかし Tree Walk 差分は将来必ず顕在化するため、両方を計算して保持する。
どちらが正しいかではなく、両方を並べて差分を見せるのが本システムの立場。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..contracts import PolicyLabel, SpecVersion
from ..records import dmarc_report_domains, dmarc_tags, find_dmarc_records

VALID_POLICIES = ("none", "quarantine", "reject")
#: RFC 9989 で削除され t= に置換されたタグ
REMOVED_IN_9989 = ("pct",)


@dataclass
class DmarcResult:
    present: bool = False
    valid: bool = False
    raw: str | None = None
    multiple_records: bool = False
    has_duplicate_tag: bool = False

    p: str | None = None
    sp: str | None = None
    np: str | None = None
    pct: int | None = None
    t: str | None = None
    psd: str | None = None
    adkim: str | None = None
    aspf: str | None = None
    rua: list[str] = field(default_factory=list)
    ruf: list[str] = field(default_factory=list)
    #: 未知タグも捨てずに保持する。round-trip のため（RFC 9989 §4.7 は
    #: 未知タグを MUST スキップとするが、保存しないとは言っていない）
    unknown_tags: dict[str, str] = field(default_factory=dict)

    effective_7489: str | None = None
    effective_9989: str | None = None
    policy_label: str | None = None
    blind_enforcement: bool = False
    spec_version: str | None = None
    notes: list[str] = field(default_factory=list)


KNOWN_TAGS = {
    "v", "p", "sp", "np", "pct", "t", "psd", "adkim", "aspf",
    "rua", "ruf", "fo", "rf", "ri",
}


def downgrade_one_step(policy: str | None) -> str | None:
    """t=y のとき1段downgradeする（RFC 9989）。

    reject -> quarantine -> none
    """
    return {"reject": "quarantine", "quarantine": "none", "none": "none"}.get(policy or "")


def classify_policy(
    p: str | None, pct: int | None, t: str | None, has_rua: bool
) -> tuple[str | None, str | None, str | None]:
    """実効強度を二通りに計算し、ラベルを付ける。

    戻り値は (RFC 7489 実効, RFC 9989 実効, ラベル)。
    """
    effective_pct = 100 if pct is None else pct
    testing = (t or "n").lower() == "y"

    # --- RFC 7489 実効強度（現在の受信側挙動に最も近い） ---
    if p == "reject":
        if effective_pct >= 100:
            eff_7489 = "reject"
        elif effective_pct > 0:
            eff_7489 = "quarantine"
        else:
            eff_7489 = "none"
    elif p == "quarantine":
        if effective_pct >= 100:
            eff_7489 = "quarantine"
        elif effective_pct == 0:
            eff_7489 = "none"
        else:
            eff_7489 = "quarantine_partial"
    else:
        eff_7489 = "none"

    # --- RFC 9989 実効強度（pct 無視、t=y で1段downgrade） ---
    eff_9989 = p if p in VALID_POLICIES else "none"
    if testing:
        eff_9989 = downgrade_one_step(eff_9989)

    return eff_7489, eff_9989, build_label(p, pct, t, has_rua)


def build_label(p: str | None, pct: int | None, t: str | None, has_rua: bool) -> str:
    """ポリシー強度の分類ラベル（DESIGN.md P5 の表）。

    名目と実効を分けて示すためのラベル。「p=reject と書いてあるが
    pct=10 なのでほぼ効いていない」を1語で表せるようにする。
    """
    testing = (t or "n").lower() == "y"
    weak_pct = pct is not None and pct < 100

    if p == "reject":
        if weak_pct:
            return PolicyLabel.NOMINAL_REJECT_WEAK_PCT
        if testing:
            return PolicyLabel.NOMINAL_REJECT_TESTING
        if not has_rua:
            # 強制しているが可視性ゼロ。何が落ちているか運用者に見えない
            return PolicyLabel.BLIND_REJECT
        return PolicyLabel.ENFORCED_REJECT

    if p == "quarantine":
        # **reject のラベルを流用しない。** quarantine を enforced_reject と
        # 呼ぶと、P7 の enforced_reject_domains に quarantine が混ざり、
        # 「reject を実効させている」という指標が別物になる
        if weak_pct:
            return PolicyLabel.NOMINAL_QUARANTINE_WEAK_PCT
        if testing:
            return PolicyLabel.NOMINAL_REJECT_TESTING
        if not has_rua:
            return PolicyLabel.BLIND_QUARANTINE
        return PolicyLabel.ENFORCED_QUARANTINE

    if p == "none":
        return PolicyLabel.MONITORING if has_rua else PolicyLabel.INEFFECTIVE

    return PolicyLabel.NONE


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value.strip())
    except (ValueError, AttributeError):
        return None


def _has_duplicate_tag(txt: str) -> bool:
    seen: set[str] = set()
    for part in txt.split(";"):
        name, sep, _ = part.partition("=")
        if not sep:
            continue
        key = name.strip().lower()
        if key in seen:
            return True
        seen.add(key)
    return False


def parse(txt_records: list[str]) -> DmarcResult:
    """_dmarc の TXT 群から DMARC を解釈する。"""
    records = find_dmarc_records(txt_records)

    if not records:
        return DmarcResult(present=False, valid=False)

    if len(records) > 1:
        # 2件以上の v=DMARC1 があればポリシー全体が無効
        return DmarcResult(
            present=True,
            valid=False,
            multiple_records=True,
            raw=records[0],
            notes=[f"v=DMARC1 が {len(records)} 件ありポリシー全体が無効"],
        )

    raw = records[0]
    tags = dmarc_tags(raw)
    p = (tags.get("p") or "").lower() or None
    pct = _to_int(tags.get("pct"))
    t = (tags.get("t") or "").lower() or None
    rua = dmarc_report_domains(raw, "rua")
    ruf = dmarc_report_domains(raw, "ruf")

    result = DmarcResult(
        present=True,
        valid=p in VALID_POLICIES,
        raw=raw,
        has_duplicate_tag=_has_duplicate_tag(raw),
        p=p,
        sp=(tags.get("sp") or "").lower() or None,
        np=(tags.get("np") or "").lower() or None,
        pct=pct,
        t=t,
        psd=(tags.get("psd") or "").lower() or None,
        adkim=(tags.get("adkim") or "").lower() or None,
        aspf=(tags.get("aspf") or "").lower() or None,
        rua=rua,
        ruf=ruf,
        unknown_tags={k: v for k, v in tags.items() if k not in KNOWN_TAGS},
    )

    eff_7489, eff_9989, label = classify_policy(p, pct, t, bool(rua))
    result.effective_7489 = eff_7489
    result.effective_9989 = eff_9989
    result.policy_label = label
    result.blind_enforcement = eff_7489 in ("reject", "quarantine") and not rua

    # spec_version は「このレコードがどちらの世代の書き方か」を表す。
    # pct や psd の有無で判別する。t= は RFC 9989 で導入された
    if pct is not None:
        result.spec_version = SpecVersion.RFC7489
    elif t is not None or result.psd is not None or result.np is not None:
        result.spec_version = SpecVersion.RFC9989
    else:
        # どちらとも取れる書き方。受信側の実勢に合わせて 7489 とする
        result.spec_version = SpecVersion.RFC7489

    if p not in VALID_POLICIES:
        result.notes.append(f"p タグが不正または欠落: {tags.get('p')!r}")
    if result.has_duplicate_tag:
        result.notes.append("重複タグがある。最初の出現値を採用した")
    if result.unknown_tags:
        result.notes.append(f"未知タグ: {sorted(result.unknown_tags)}（保持のみ、評価しない）")
    if result.blind_enforcement:
        result.notes.append("強制ポリシーだが rua が無い。何が落ちているか可視化されていない")
    if eff_7489 != eff_9989:
        result.notes.append(
            f"RFC 7489 と RFC 9989 で実効強度が異なる: {eff_7489} / {eff_9989}"
        )
    return result


def report_domain_is_external(policy_domain: str, report_domain: str) -> bool:
    """rua/ruf の宛先が外部ドメインか。

    外部なら `<reporting-domain>._report._dmarc.<external-domain>` に
    v=DMARC1 があるかを検証する必要がある（External Destination Verification）。
    """
    return report_domain.lower().rstrip(".") != policy_domain.lower().rstrip(".")


def authorization_record_name(policy_domain: str, external_domain: str) -> str:
    """External Destination Verification のために引く名前。"""
    return f"{policy_domain.rstrip('.')}._report._dmarc.{external_domain.rstrip('.')}"
