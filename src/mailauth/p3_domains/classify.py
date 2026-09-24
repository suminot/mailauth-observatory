"""確度フラグの判定（DESIGN.md P3）。

**ここが本システムの中核**である。企業とドメインの対応を、確度を明示して
公開することが本システムの中心的な貢献であり、その判定がここに集まる。

判定ロジックは DESIGN.md の擬似コードに忠実だが、1点だけ補っている。
仕様の擬似コードは `spf_hard_deny` を参照しているのに引数に持っていなかった
ため、明示的な引数として受け取るようにした。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..contracts import Confidence, DomainRole, MeasureTier

#: 証拠の強さ（DESIGN.md P3「証拠の優先順位」）
#:   DKIM CNAME >= MX > SPF include > 所有権確認TXT
EVIDENCE_WEIGHT = {
    "dkim_cname": 4,
    "mx": 3,
    "spf_include": 2,
    "verification_txt": 1,
}


@dataclass
class Probe:
    """1ドメインの一次実証の結果。"""

    domain: str
    #: 原則5。DNS が引けたかどうか
    observed: bool = True
    mx_exists: bool = False
    null_mx: bool = False
    mx_hosts: list[str] = field(default_factory=list)
    spf_exists: bool = False
    spf_aligned: bool = False
    spf_all_qualifier: str | None = None
    spf_includes: list[str] = field(default_factory=list)
    dmarc_exists: bool = False
    dkim_found: bool = False
    #: 発見経路のうち、DNS 一次実証から独立しているものの数
    independent_evidence_count: int = 0
    discovery_methods: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def spf_hard_deny(self) -> bool:
        """`v=spf1 -all`（送信全否定）。設定漏れではなく意図的な宣言。"""
        return self.spf_exists and self.spf_all_qualifier == "-"

    @property
    def spf_soft_deny(self) -> bool:
        return self.spf_exists and self.spf_all_qualifier in ("-", "~")


def classify_confidence(
    mx_exists: bool,
    spf_aligned: bool,
    dkim_found: bool,
    dmarc_exists: bool,
    independent_evidence_count: int,
    spf_hard_deny: bool = False,
) -> str:
    """三段の確度フラグを付ける。

    Confirmed: MX実在 かつ From整合するSPFまたはDKIMを確認
               かつ 独立ソースでの裏付けが1件以上
    Likely:    MX実在 かつ SPF/DMARC存在 だが独立裏付けが不足
               または 子ドメイン/グループ会社ドメイン
    Parked:    MX不在 かつ SPF で送信全否定を宣言
    Unknown:   MX不在、または一次実証なし

    MX が無くても SPF で -all を宣言していれば「意図的な送信禁止」であって
    設定漏れではない。Czybik et al.（IMC 2023）は MX の無いドメインの
    10.4% が SPF を持ち、うち 53.1% が `-all`/`~all` だと報告している。
    MXフィルタを機械的に適用するとこの意図を見落とす。
    """
    if not mx_exists:
        return Confidence.PARKED if spf_hard_deny else Confidence.UNKNOWN

    primary = spf_aligned or dkim_found
    if primary and independent_evidence_count >= 1:
        return Confidence.CONFIRMED
    if primary or dmarc_exists:
        return Confidence.LIKELY
    return Confidence.UNKNOWN


def classify_role(domain: str, official_domain: str | None, confidence: str) -> str:
    """primary / related / parked を決める。

    集計時に「主ドメインだけ」と「関連も含む」の両方の数字を出せるように
    区別しておく（DESIGN.md P2「展開の深さ」）。
    """
    if confidence == Confidence.PARKED:
        return DomainRole.PARKED
    if official_domain and domain == official_domain:
        return DomainRole.PRIMARY
    return DomainRole.RELATED


def assign_tier(confidence: str, measure_cfg: dict | None = None) -> str:
    """P4 の計測の深さ。

    階層A（フル）に confirmed / likely、階層C（簡易）にそれ以外。
    送信していないドメインに DKIM セレクタを50個投げても検出されないし、
    権威DNSへの負荷という点で作法が悪い（DESIGN.md P2）。

    **どの確度が階層A に入るかは `measure.yaml` が決める。** 以前はここに
    直書きされていて、設定の `applies_to_confidence` を書き換えても何も
    変わらなかった（原則7 が破れていた）。設定に無ければ上の既定に戻る。
    """
    from ..config import load_measure_config

    cfg = measure_cfg if measure_cfg is not None else load_measure_config()
    spec = ((cfg.get("tiers") or {}).get(MeasureTier.A) or {}).get("applies_to_confidence")
    if spec:
        return MeasureTier.A if confidence in set(spec) else MeasureTier.C
    if confidence in (Confidence.CONFIRMED, Confidence.LIKELY):
        return MeasureTier.A
    return MeasureTier.C


def build_evidence(probe: Probe) -> list[dict[str, Any]]:
    """判定根拠の配列。あとから「なぜこの判定になったか」を追跡できるように。"""
    evidence: list[dict[str, Any]] = []
    if probe.mx_exists:
        evidence.append(
            {
                "type": "mx",
                "weight": EVIDENCE_WEIGHT["mx"],
                "value": probe.mx_hosts[:5],
            }
        )
    if probe.null_mx:
        evidence.append({"type": "null_mx", "weight": 0, "value": "0 ."})
    if probe.spf_exists:
        evidence.append(
            {
                "type": "spf",
                "weight": EVIDENCE_WEIGHT["spf_include"],
                "value": {
                    "all": probe.spf_all_qualifier,
                    "includes": probe.spf_includes[:10],
                    "aligned": probe.spf_aligned,
                },
            }
        )
    if probe.dmarc_exists:
        evidence.append({"type": "dmarc", "weight": 0, "value": "present"})
    for method in probe.discovery_methods:
        evidence.append({"type": "discovery", "weight": 0, "value": method})
    for failure in probe.failures:
        # 「取れなかった」ことも根拠として残す（原則5）
        evidence.append({"type": "query_failed", "weight": 0, "value": failure})
    return evidence


def evidence_count(probe: Probe) -> int:
    """重み付きの証拠数。強い証拠を多く持つドメインを上位に置くために使う。"""
    total = 0
    if probe.dkim_found:
        total += EVIDENCE_WEIGHT["dkim_cname"]
    if probe.mx_exists:
        total += EVIDENCE_WEIGHT["mx"]
    if probe.spf_exists:
        total += EVIDENCE_WEIGHT["spf_include"]
    return total
