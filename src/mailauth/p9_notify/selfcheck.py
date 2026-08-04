"""送信元自身のメール認証の自己検査（DESIGN.md Sprint 9）。

**送信元自身の SPF/DKIM/DMARC を完全準拠させること。通知者の認証設定が
問われる。** メール認証の不備を指摘する通知を、自分の設定が不備な状態で
送ると、その一通で信頼を失う。しかも相手には「自分もできていないのに」と
返す正当な理由を与えてしまう。

だから通知計画の生成そのものを、この検査に依存させる。**通らなければ
計画を作らない。** 警告では済ませない。

検査には既存の計測系をそのまま使う。自分のドメインも他社と同じ手続きで
測る。特別扱いすると、他社に適用している基準と食い違う。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..p5_parse import dkim as dkim_mod
from ..p5_parse import dmarc as dmarc_mod
from ..p5_parse import spf as spf_mod
from ..records import join_txt_strings
from ..resolver import Resolver

#: 通知を出す資格として要求する条件。**どれも譲らない。**
REQUIREMENTS = (
    "spf_present",
    "spf_valid",
    "spf_all_hard_or_soft",
    "dmarc_present",
    "dmarc_enforced",
    "dmarc_has_rua",
    "dkim_detected",
)


class SelfComplianceError(RuntimeError):
    """送信元の認証が要件を満たしていない。**通知計画を作らせない。**"""


@dataclass
class SelfComplianceResult:
    domain: str
    checks: dict[str, bool | None] = field(default_factory=dict)
    details: dict[str, str | None] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def compliant(self) -> bool:
        return all(self.checks.get(name) is True for name in REQUIREMENTS)

    @property
    def failed(self) -> list[str]:
        return [name for name in REQUIREMENTS if self.checks.get(name) is not True]

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "compliant": self.compliant,
            "checks": dict(self.checks),
            "failed": self.failed,
            "details": dict(self.details),
            "notes": self.notes,
        }


def check_self(
    domain: str, *, resolver: Resolver, selectors: list[str] | None = None
) -> SelfComplianceResult:
    """通知の送信元ドメインを測る。

    `selectors` を渡さないと DKIM は「既知セレクタで確認していない」に
    なり、要件を満たさない。**セレクタを知っているのは自分なので、
    自分のドメインについては明示的に渡すべきである。**
    """
    result = SelfComplianceResult(domain=domain)

    # -- SPF ---------------------------------------------------------------
    apex = resolver.query(domain, "TXT")
    spf_texts = [join_txt_strings(c) for c in apex.txt_strings] or apex.values
    if not apex.observed:
        result.notes.append(
            f"{domain} の TXT を観測できなかった。"
            "「レコードが無い」ではなく「取れなかった」（原則5）"
        )
        for name in REQUIREMENTS:
            result.checks[name] = None
        return result

    spf = spf_mod.parse(spf_texts)
    result.checks["spf_present"] = bool(spf.present)
    result.checks["spf_valid"] = bool(spf.valid)
    result.checks["spf_all_hard_or_soft"] = spf.all_qualifier in ("-", "~")
    result.details["spf"] = spf.raw
    if spf.all_qualifier not in ("-", "~"):
        result.notes.append(
            "SPF の all が -all / ~all になっていない。"
            "他社に指摘する立場では譲れない条件である"
        )

    # -- DMARC -------------------------------------------------------------
    dmarc_answer = resolver.query(f"_dmarc.{domain}", "TXT")
    dmarc_texts = [
        join_txt_strings(c) for c in dmarc_answer.txt_strings
    ] or dmarc_answer.values
    dmarc = dmarc_mod.parse(dmarc_texts)
    result.checks["dmarc_present"] = bool(dmarc.present)
    result.checks["dmarc_enforced"] = dmarc.effective_7489 in ("reject", "quarantine")
    result.checks["dmarc_has_rua"] = bool(dmarc.rua)
    result.details["dmarc"] = dmarc.raw
    result.details["policy_label"] = dmarc.policy_label
    if not dmarc.rua:
        result.notes.append(
            "rua が無い。強制していても何が拒否されているか分からない状態で"
            "他社に通知するのは筋が通らない"
        )
    if dmarc.effective_7489 == "none":
        result.notes.append(
            "DMARC が p=none。spoofing 抑止効果は限定的であり、"
            "この状態で他社に通知しても説得力を持たない"
        )

    # -- DKIM --------------------------------------------------------------
    found: dict[str, str] = {}
    tried = 0
    for selector in selectors or []:
        tried += 1
        answer = resolver.query(f"{selector}._domainkey.{domain}", "TXT")
        if answer.observed and answer.record_present:
            texts = [join_txt_strings(c) for c in answer.txt_strings] or answer.values
            if texts:
                found[selector] = texts[0]
    dkim = dkim_mod.build_result(
        found=found, selectors_tried=tried, control_responded=False,
        applicable=tried > 0,
    )
    result.checks["dkim_detected"] = dkim.status == "detected"
    result.details["dkim_selectors"] = ",".join(dkim.selectors) or None
    if not selectors:
        result.notes.append(
            "DKIM のセレクタが渡されていないため確認していない。"
            "**自分のセレクタは自分が知っているので、明示的に設定すること**"
        )

    return result


def require_compliant(result: SelfComplianceResult) -> None:
    """満たしていなければ止める。**警告では済ませない。**"""
    if result.compliant:
        return
    raise SelfComplianceError(
        f"送信元 {result.domain} のメール認証が要件を満たしていない: "
        f"{', '.join(result.failed)}。\n"
        "メール認証の不備を指摘する通知を、自分の設定が不備な状態で送ると"
        "その一通で信頼を失う。先に自分の設定を直すこと。\n  "
        + "\n  ".join(result.notes)
    )
