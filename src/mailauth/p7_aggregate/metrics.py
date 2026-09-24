"""指標の計算（DESIGN.md P7）。

守っていること
  - **企業数ベースとドメインベースの両方を出す**（受け入れ基準）。
    ドメイン数だけで語ると、ドメインを多く持つ大企業の重みが不当に増す。
  - **名目と実効を分ける。** `p=reject` と書いてあることと、それが実際に
    効いていることは別の事実（pct / t=y / rua 無し）。
  - **採用率の分母は観測できたドメインだけ。** 取れなかったドメインを
    分母に入れると「取れなかった」が「未対応」に化ける（原則5）。
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ..contracts import DkimStatus, ParkClass, PolicyLabel, StatsOverall
from ..p5_parse.extras import maturity_stage

#: 強制と見なす DMARC ポリシー
ENFORCING = ("reject", "quarantine")


def _truthy(value: Any) -> bool:
    """parquet 由来の NaN / None / numpy bool を素の bool にする。"""
    if value is None:
        return False
    if isinstance(value, float):
        return value == value and bool(value)  # NaN は False
    return bool(value)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value != value:
        return None
    return str(value)


@dataclass
class DomainRow:
    """集計に必要な範囲だけ抜いた fact。列が増えても P7 を触らずに済む。"""

    domain_id: str
    entity_id: str
    observed: bool
    spf_present: bool
    dmarc_present: bool
    effective_7489: str | None
    dmarc_p: str | None
    policy_label: str | None
    dkim_status: str | None
    mta_sts_present: bool
    tls_rpt_present: bool
    bimi_present: bool
    bimi_has_vmc: bool
    dnssec_signed: bool
    dane_present: bool
    dane_orphan: bool
    park_class: str | None = None

    @classmethod
    def from_fact(cls, fact: dict) -> DomainRow:
        return cls(
            domain_id=str(fact.get("domain_id") or ""),
            entity_id=str(fact.get("entity_id") or ""),
            observed=_truthy(fact.get("observed")),
            spf_present=_truthy(fact.get("spf_present")),
            dmarc_present=_truthy(fact.get("dmarc_present")),
            effective_7489=_text(fact.get("effective_7489")),
            dmarc_p=_text(fact.get("dmarc_p")),
            policy_label=_text(fact.get("policy_label")),
            dkim_status=_text(fact.get("dkim_status")),
            mta_sts_present=_truthy(fact.get("mta_sts_present")),
            tls_rpt_present=_truthy(fact.get("tls_rpt_present")),
            bimi_present=_truthy(fact.get("bimi_present")),
            bimi_has_vmc=_truthy(fact.get("bimi_has_vmc")),
            dnssec_signed=_truthy(fact.get("dnssec_signed")),
            dane_present=_truthy(fact.get("dane_present")),
            dane_orphan=_truthy(fact.get("dane_orphan")),
        )

    @property
    def dmarc_enforced(self) -> bool:
        return self.effective_7489 in ENFORCING

    @property
    def stage(self) -> int:
        return maturity_stage(
            spf_present=self.spf_present,
            dkim_detected=self.dkim_status == DkimStatus.DETECTED,
            dmarc_present=self.dmarc_present,
            dmarc_enforced=self.dmarc_enforced,
            mta_sts_present=self.mta_sts_present,
            tls_rpt_present=self.tls_rpt_present,
            dnssec_signed=self.dnssec_signed,
            bimi_with_vmc=self.bimi_present and self.bimi_has_vmc,
            dane_present=self.dane_present,
        )


@dataclass
class Aggregation:
    stats: StatsOverall
    #: 集計に使ったドメインの entity_id 集合。業種別との整合確認に使う
    entity_ids: set[str] = field(default_factory=set)


def park_classes(inferences: list[dict]) -> dict[str, str]:
    """domain_id -> park_class。

    P6 はパーク分類をドメインの先頭行にだけ載せている（二重計数を避けるため）。
    ここでは非 null の値を拾う。
    """
    out: dict[str, str] = {}
    for row in inferences:
        value = _text(row.get("park_class"))
        if value:
            out[str(row.get("domain_id") or "")] = value
    return out


def aggregate(
    rows: list[DomainRow],
    *,
    measured_month,
    population_id: str,
    total_entities: int,
    spec_version: str | None = None,
) -> StatsOverall:
    """ドメイン行から全社統計を1件作る。

    `total_entities` は母集団側の企業数を渡す。**ドメインを持つ企業だけを
    数えてはいけない。** 分母が縮むと採用率が実態より高く出る。

    その代わり `entities_with_domains` で「実際に計測に現れた企業数」を別に
    出す。**分母を正しく保つことと、読み手にその中身を見せることは別の
    仕事である** ── 差を出さないと、読み手は total_entities 社を測ったと読む。
    """
    observed = [r for r in rows if r.observed]
    # 観測できたかは問わない。**候補が1件でもあれば「計測に現れた」**
    # （SERVFAIL は「取れなかった」であって、起点が無いのとは別の欠け方）
    with_domains = len({r.entity_id for r in rows if r.entity_id})

    def entities_where(predicate) -> int:
        return len({r.entity_id for r in observed if predicate(r)})

    stages = Counter(r.stage for r in observed)
    parked = [r for r in observed if r.park_class in _PARKED_CLASSES]
    hardened = sum(1 for r in parked if r.park_class == ParkClass.HARDENED_PARKED)
    defended = sum(1 for r in parked if r.park_class == ParkClass.DEFENDED_PARKED)
    sending = [r for r in observed if r.park_class == ParkClass.ACTIVE_SENDING]

    return StatsOverall(
        measured_month=measured_month,
        population_id=population_id,
        total_entities=total_entities,
        total_domains=len(rows),
        observed_domains=len(observed),
        entities_with_domains=with_domains,
        spf_adopted_entities=entities_where(lambda r: r.spf_present),
        spf_adopted_domains=sum(1 for r in observed if r.spf_present),
        dmarc_adopted_entities=entities_where(lambda r: r.dmarc_present),
        dmarc_adopted_domains=sum(1 for r in observed if r.dmarc_present),
        dmarc_enforced_entities=entities_where(lambda r: r.dmarc_enforced),
        dmarc_enforced_domains=sum(1 for r in observed if r.dmarc_enforced),
        # 名目は「そう書いてある」、実効は「それが効いている」
        nominal_reject_domains=sum(1 for r in observed if r.dmarc_p == "reject"),
        enforced_reject_domains=sum(
            1 for r in observed if r.policy_label == PolicyLabel.ENFORCED_REJECT
        ),
        blind_reject_domains=sum(
            1 for r in observed if r.policy_label == PolicyLabel.BLIND_REJECT
        ),
        dkim_detected_domains=sum(
            1 for r in observed if r.dkim_status == DkimStatus.DETECTED
        ),
        # 「未設定」ではない。既知セレクタでは見つからなかった、である
        dkim_not_found_domains=sum(
            1
            for r in observed
            if r.dkim_status == DkimStatus.NOT_FOUND_IN_KNOWN_SELECTORS
        ),
        mta_sts_domains=sum(1 for r in observed if r.mta_sts_present),
        tls_rpt_domains=sum(1 for r in observed if r.tls_rpt_present),
        bimi_domains=sum(1 for r in observed if r.bimi_present),
        dnssec_domains=sum(1 for r in observed if r.dnssec_signed),
        maturity_stage_dist=json.dumps(
            {str(k): stages.get(k, 0) for k in range(5)}, sort_keys=True
        ),
        sending_domains=len(sending),
        sending_enforced=sum(1 for r in sending if r.dmarc_enforced),
        parked_domains=len(parked),
        parked_hardened=hardened,
        parked_defended=defended,
        parked_intentional=sum(
            1 for r in parked if r.park_class == ParkClass.INTENTIONAL_NO_SEND
        ),
        parked_neglected=sum(1 for r in parked if r.park_class == ParkClass.NEGLECTED),
        park_defense_rate=(
            round((hardened + defended) / len(parked), 4) if parked else None
        ),
        dane_domains=sum(1 for r in observed if r.dane_present),
        # TLSA があり親ゾーンも署名済み。orphan は「実効しない誤設定」なので除く
        dane_dnssec_valid=sum(
            1 for r in observed if r.dane_present and r.dnssec_signed and not r.dane_orphan
        ),
        dane_orphan=sum(1 for r in observed if r.dane_orphan),
        spec_version=spec_version,
    )


_PARKED_CLASSES = (
    ParkClass.HARDENED_PARKED,
    ParkClass.DEFENDED_PARKED,
    ParkClass.INTENTIONAL_NO_SEND,
    ParkClass.NEGLECTED,
)
