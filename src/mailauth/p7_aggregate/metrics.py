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
import re
from collections import Counter, defaultdict
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


def _truthy_or(value: Any, *, default: bool) -> bool:
    """欠けている値を `default` にする。

    `_truthy` は欠けを False にする。**「列がまだ無い」を False として
    扱ってよい列と、そうでない列がある** ── `is_apex` は既定が True で、
    False にすると過去の run のドメインが全部サブドメイン扱いになる。
    """
    if value is None:
        return default
    if isinstance(value, float) and value != value:  # NaN
        return default
    return bool(value)


_VALID_DMARC_P = frozenset({"none", "quarantine", "reject"})


def _nonempty(values: Any) -> bool:
    """list・numpy 配列に要素があるか。None・NaN・文字列は False。"""
    if values is None or isinstance(values, (str, bytes, float)):
        return False
    try:
        return len(values) > 0
    except TypeError:
        return False


def _min_int(values: Any) -> int | None:
    """list・numpy 配列・None から、いちばん小さい整数。"""
    if values is None or isinstance(values, (str, bytes)):
        return None
    try:
        nums = [int(v) for v in values if v is not None and v == v]
    except TypeError:
        return None
    return min(nums) if nums else None


def _opt_int(value: Any) -> int | None:
    """整数か None（欠損・NaN は None）。"""
    if value is None or isinstance(value, (str, bytes, bool)):
        return None
    try:
        return int(value) if value == value else None
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value != value:
        return None
    return str(value)



def _tristate(value: object) -> bool | None:
    """True / False / None（欠損・NaN は None）。**欠損を False にしない**（原則5）。"""
    if value is None:
        return None
    if isinstance(value, float) and value != value:  # NaN
        return None
    return bool(value)


#: UDP で切れずに返る目安（DNS Flag Day 2020 の EDNS バッファ 1,232 バイト）
TXT_UDP_SAFE_BYTES = 1232
#: SPF が通す IPv4 がこれを超えると「広い」と数える（/16 1つ分）
SPF_IP4_BROAD = 65536


#: 例示用に予約された名前（RFC 2606・RFC 6761）。**これだけを雛形とみなす。**
#: `yourdomain.com` のような名前は誰かが登録していることがあり、断定できない
_RESERVED_EXAMPLE = frozenset({"example.com", "example.net", "example.org"})
_RESERVED_TLDS = frozenset({"example", "invalid", "test", "localhost"})


def is_placeholder_domain(domain: str) -> bool:
    d = domain.strip().lower().rstrip(".")
    if not d:
        return False
    if d in _RESERVED_EXAMPLE or any(d.endswith("." + x) for x in _RESERVED_EXAMPLE):
        return True
    return d.rsplit(".", 1)[-1] in _RESERVED_TLDS


def _spf_usable(r) -> bool:
    """SPF があり、評価がエラーで終わらない（2つある・上限超えを除く）。"""
    return (
        r.spf_present
        and r.spf_error not in ("multiple_records", "permerror")
        and r.spf_total_exceeds is not True
    )


def _dmarc_usable(r) -> bool:
    """DMARC があり、レコードが有効（1つだけ、p= が正しい）。p=none でよい。"""
    return r.dmarc_present and not r.dmarc_multiple_records and r.dmarc_p in _VALID_DMARC_P


#: 会社とドメインを対応づけた根拠の強さの順（強いものから）。P2 の経路を束ねる
OWNERSHIP_TIERS = ("company_stated", "certificate", "own_dns", "web_link", "other")
_OWNERSHIP_OF_METHOD = {
    # 会社が自分で書いた（公式サイト・有報の公告・年次報告書・運営者の確認）
    "official_url": "company_stated",
    "official_alt": "company_stated",
    "edinet_koukoku": "company_stated",
    "manual": "company_stated",
    # 同じ証明書に載っている・証明書の組織名がその会社
    "ct_org": "certificate",
    "ct_log": "certificate",
    "tls_san": "certificate",
    "ct_mail_name": "certificate",
    # 会社の DNS が指している（SPF の redirect・自社の include・DMARC の rua）
    "spf_redirect": "own_dns",
    "spf_include": "own_dns",
    "dmarc_rua": "own_dns",
    # 公式サイトのリンク先で、ページのタイトルに商号がある
    "site_link": "web_link",
    "sibling_tld": "web_link",
}


def ownership_tier(methods) -> str | None:
    """経路の一覧から、いちばん強い根拠の区分。経路が無ければ None。"""
    tiers = {_OWNERSHIP_OF_METHOD.get(str(m), "other") for m in (methods or [])}
    for tier in OWNERSHIP_TIERS:
        if tier in tiers:
            return tier
    return None


def domain_stage(r: DomainRow) -> str:
    """1ドメインの「メールに使っている証拠」の段階（`mail_evidence` と同じ区切り）。"""
    if not r.mx_reachable:
        return "none"
    if not r.spf_sends:
        return "receiving"
    return "signed" if r.dkim_status == DkimStatus.DETECTED else "sending"


def ownership_evidence(observed: list[DomainRow]) -> str | None:
    """根拠の区分 × 証拠の段階のドメイン数（JSON）。根拠が1つも無ければ None。"""
    rows = [r for r in observed if r.ownership]
    if not rows:
        return None
    table = {t: {s: 0 for s in ("signed", "sending", "receiving", "none")}
             for t in OWNERSHIP_TIERS}
    for r in rows:
        table[r.ownership][domain_stage(r)] += 1
    return json.dumps(table, sort_keys=True)


def mail_evidence(observed: list[DomainRow]) -> dict[str, int]:
    """会社ごとに「メールに使っている証拠」がどこまであるか（apex の観測行から）。

    段階は排他で、上から順に当てる。

      signed    : どれかのドメインで、受け取れる MX・SPF の送信元・DKIM の鍵がそろう
      sending   : MX と SPF の送信元はあるが、DKIM は試したセレクタで見つからない
      receiving : MX はあるが、SPF に送信元を書いていない
      none      : どのドメインにも受け取れる MX が無い

    **DNS に設定があることは、実際に送っている証明ではない。** それでも、
    受け取りと送信と署名の設定がそろうドメインを、使っていないと見る理由は薄い。

    `off_site`：公式サイトのドメインには受け取れる MX が無いが、同じ会社の
    別のドメインにはある。**公式サイトのドメインだけを測る方法では、この会社の
    メールを測れない**（TwoFive などの公開調査と比べるときの差）。
    """
    by_entity: dict[str, list[DomainRow]] = defaultdict(list)
    for r in observed:
        if r.entity_id:
            by_entity[r.entity_id].append(r)
    out = {"signed": 0, "sending": 0, "receiving": 0, "none": 0, "off_site": 0,
           "sending_dkim_hint": 0}
    for rs in by_entity.values():
        mx = any(r.mx_reachable for r in rs)
        send = mx and any(r.spf_sends for r in rs)
        signed = send and any(r.dkim_status == DkimStatus.DETECTED for r in rs)
        if signed:
            out["signed"] += 1
        elif send:
            out["sending"] += 1
            # そのうち `_domainkey` の配下に名前がある（試していないセレクタの鍵がありそう）
            if any(r.dkim_domainkey_exists is True for r in rs):
                out["sending_dkim_hint"] += 1
        elif mx:
            out["receiving"] += 1
        else:
            out["none"] += 1
        official = [r for r in rs if r.is_official]
        if official and not any(r.mx_reachable for r in official) and any(
            r.mx_reachable for r in rs if not r.is_official
        ):
            out["off_site"] += 1
    return out


def _count_true(rows, base, value) -> int | None:
    """`base` を満たす行のうち `value` が True の数。**値が1つも無ければ None**。"""
    vals = [value(r) for r in rows if base(r)]
    if not any(v is not None for v in vals):
        return None
    return sum(1 for v in vals if v is True)


@dataclass
class DomainRow:
    """集計に必要な範囲だけ抜いた fact。列が増えても P7 を触らずに済む。"""

    domain_id: str
    entity_id: str
    is_apex: bool
    observed: bool
    spf_present: bool
    dmarc_present: bool
    effective_7489: str | None
    applied_7489: str | None
    sp_effective_7489: str | None
    sp_weaker: bool
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
    #: HTTPS で取ったポリシーの mode。取っていない run では null
    mta_sts_mode: str | None = None
    mta_sts_policy_status: str | None = None
    mta_sts_mx_mismatch: bool = False
    #: rua に外部の宛先がある / 宛先の認可レコード / 宛先ドメインの登録
    #: （P5。三値：引けなかったものは None）
    rua_external: bool = False
    rua_authorized: bool | None = None
    rua_domain_unregistered: bool | None = None
    #: SPF を include の先まで数えて上限を超えるか / 先が壊れているか（P5。三値）
    spf_total_exceeds: bool | None = None
    spf_target_broken: bool | None = None
    #: BACKLOG 49（P5 1.4.0）。三値。列の無い過去の run は None
    spf_target_unregistered: bool | None = None
    txt_apex_bytes: int | None = None
    dkim_ed25519_only: bool | None = None
    dmarc_historic_tags: bool | None = None
    spf_ip4_addresses: int | None = None
    dkim_domainkey_exists: bool | None = None
    #: SPF に ptr がある（P5 1.7.0）。列の無い過去の run は None
    spf_has_ptr: bool | None = None
    #: DMARC に ruf（失敗レポートの宛先）がある
    dmarc_has_ruf: bool = False
    #: メールを受け取れる（MX があり、Null MX でなく、どれかの MX に宛先がある）
    mx_reachable: bool = False
    #: SPF に送信元を書いている（include・ip4/ip6・a・mx など。-all だけではない）
    spf_sends: bool = False
    #: その会社の公式サイトのドメインか（P7 の runner が付ける）
    is_official: bool = False
    #: 会社とドメインを対応づけた根拠のうち、いちばん強いもの（P3 の経路から。
    #: `OWNERSHIP_TIERS` のどれか。P3 の出力が無い run では None）
    ownership: str | None = None
    #: DMARC の np=（存在しないサブドメイン）と t=（試行中）。RFC 9989 で入ったタグ
    dmarc_np: str | None = None
    dmarc_t: str | None = None
    #: 最後の `all` の修飾子（- ~ ? +）。無ければ None
    spf_all_qualifier: str | None = None
    spf_error: str | None = None
    #: redirect の先を引けず、最後に効く all が分からない
    spf_all_unknown: bool = False
    dmarc_multiple_records: bool = False
    #: 見つかった RSA 鍵のうち最も短いものの長さ。無ければ None
    dkim_min_key_bits: int | None = None
    #: DMARC の rua= に宛先が1つでもあるか
    dmarc_has_rua: bool = False
    #: rua の宛先に、例示用に予約されたドメイン（RFC 2606・6761）がある
    rua_placeholder: bool = False

    @classmethod
    def from_fact(cls, fact: dict) -> DomainRow:
        return cls(
            domain_id=str(fact.get("domain_id") or ""),
            entity_id=str(fact.get("entity_id") or ""),
            # 列が無い過去の run も読めるようにする（既定は apex）
            is_apex=_truthy_or(fact.get("is_apex"), default=True),
            observed=_truthy(fact.get("observed")),
            spf_present=_truthy(fact.get("spf_present")),
            dmarc_present=_truthy(fact.get("dmarc_present")),
            effective_7489=_text(fact.get("effective_7489")),
            applied_7489=_text(fact.get("applied_7489")),
            sp_effective_7489=_text(fact.get("sp_effective_7489")),
            sp_weaker=_truthy(fact.get("sp_weaker")),
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
            mta_sts_mode=_text(fact.get("mta_sts_mode")),
            mta_sts_policy_status=_text(fact.get("mta_sts_policy_status")),
            mta_sts_mx_mismatch=_truthy(fact.get("mta_sts_mx_mismatch")),
            rua_external=_truthy(fact.get("rua_external")),
            rua_authorized=_tristate(fact.get("rua_authorized")),
            rua_domain_unregistered=_tristate(fact.get("rua_domain_unregistered")),
            spf_total_exceeds=_tristate(fact.get("spf_total_exceeds")),
            spf_target_broken=_tristate(fact.get("spf_target_broken")),
            spf_target_unregistered=_tristate(fact.get("spf_target_unregistered")),
            txt_apex_bytes=_opt_int(fact.get("txt_apex_bytes")),
            dkim_ed25519_only=_tristate(fact.get("dkim_ed25519_only")),
            dmarc_historic_tags=_tristate(fact.get("dmarc_historic_tags")),
            spf_ip4_addresses=_opt_int(fact.get("spf_ip4_addresses")),
            dkim_domainkey_exists=_tristate(fact.get("dkim_domainkey_exists")),
            mx_reachable=_truthy(fact.get("mx_present"))
            and not _truthy(fact.get("mx_null"))
            and not _truthy(fact.get("mx_unreachable")),
            spf_sends=_truthy(fact.get("spf_present"))
            and (
                _nonempty(fact.get("spf_includes"))
                or _nonempty(fact.get("spf_mechanisms"))
                or bool(re.search(r"(?i)(^|\s)[+]?ip[46]:", str(fact.get("raw_spf") or "")))
            ),
            dmarc_np=_text(fact.get("dmarc_np")),
            dmarc_t=_text(fact.get("dmarc_t")),
            # **最後に効く all を優先する**（redirect の先の all）。列が無い過去の
            # run では自分のレコードの all
            spf_all_qualifier=(
                _text(fact.get("spf_all_effective"))
                if "spf_all_effective" in fact
                else _text(fact.get("spf_all_qualifier"))
            ),
            spf_all_unknown=_truthy(fact.get("spf_all_unknown")),
            spf_error=_text(fact.get("spf_error")),
            dmarc_multiple_records=_truthy(fact.get("dmarc_multiple_records")),
            dkim_min_key_bits=_min_int(fact.get("dkim_key_bits")),
            dmarc_has_rua=_nonempty(fact.get("dmarc_rua")),
            dmarc_has_ruf=_nonempty(fact.get("dmarc_ruf")),
            spf_has_ptr=_tristate(fact.get("spf_has_ptr")),
            rua_placeholder=_nonempty(fact.get("dmarc_rua"))
            and any(is_placeholder_domain(str(d)) for d in fact.get("dmarc_rua")),
        )

    @property
    def dmarc_enforced(self) -> bool:
        return self.effective_7489 in ENFORCING

    @property
    def applied_enforced(self) -> bool:
        """**実際に効いている強度**が quarantine 以上か。

        自分の `_dmarc` があればその値、無ければ上位から継承した値。
        `applied_7489` が埋まっていない古い run では `effective_7489`
        に落とす ── 列が無いことを「効いていない」と読まないため。
        """
        applied = self.applied_7489 or self.effective_7489
        return applied in ENFORCING

    @property
    def subdomain_enforced(self) -> bool:
        """**配下のサブドメインに効く強度。** `sp=` が無ければ `p=` を継承する。

        `dmarc_enforced` と別に持つ。`p=reject; sp=none` の会社を
        「reject 達成」と数えると、実際のメールドメインがサブドメインの
        場合に無防備なものを達成側に入れてしまう。
        """
        return self.sp_effective_7489 in ENFORCING

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
    # **分母は apex で閉じる**（BACKLOG 14、運営者の判断は「別枠」）。
    #
    # サブドメインを混ぜるとドメイン数が増え、前月との比較が効かなくなる ──
    # 母集団を変えて計測し直すのと同じことになる。サブドメインの数字は
    # `subdomains_*` に別枠で出す。
    #
    # **この1行が契約そのものである。** ここを外すと、下のすべての指標の
    # 分母が黙って変わる
    subdomains = [r for r in rows if not r.is_apex]
    rows = [r for r in rows if r.is_apex]

    observed = [r for r in rows if r.observed]
    # 観測できたかは問わない。**候補が1件でもあれば「計測に現れた」**
    # （SERVFAIL は「取れなかった」であって、起点が無いのとは別の欠け方）
    with_domains = len({r.entity_id for r in rows if r.entity_id})
    subdomains_observed = [r for r in subdomains if r.observed]

    def entities_where(predicate) -> int:
        return len({r.entity_id for r in observed if predicate(r)})

    # MTA-STS のポリシーを取った run か。**TXT のあるドメインのうち1つでも
    # 取っていなければ、mode の数は出さない**（0 と書くと「enforce が無い」に読める）
    policy_fetched = not any(
        r.mta_sts_present and r.mta_sts_policy_status is None for r in observed
    )
    # SPF の先をたどった run か。**たどっていない run（resolver 無し・過去の
    # silver）では 0 ではなく null**。SPF があるのに両方 null なら、たどっていない
    spf_walked = any(
        r.spf_total_exceeds is not None or r.spf_target_broken is not None for r in observed
    )
    # **SPF が1つだけのもの。** v=spf1 が2つあるとレコード全体が無効で、
    # `all` を読む意味が無い（RFC 7208 §4.5）
    spf_single = [
        r for r in observed if r.spf_present and r.spf_error != "multiple_records"
    ]
    all_q = Counter(r.spf_all_qualifier for r in spf_single if not r.spf_all_unknown)
    dkim_keys = [
        r.dkim_min_key_bits
        for r in observed
        if r.dkim_status == DkimStatus.DETECTED and r.dkim_min_key_bits is not None
    ]
    stages = Counter(r.stage for r in observed)
    parked = [r for r in observed if r.park_class in _PARKED_CLASSES]
    hardened = sum(1 for r in parked if r.park_class == ParkClass.HARDENED_PARKED)
    defended = sum(1 for r in parked if r.park_class == ParkClass.DEFENDED_PARKED)
    sending = [r for r in observed if r.park_class == ParkClass.ACTIVE_SENDING]
    evidence = mail_evidence(observed)
    # **大口送信者の要件のうち DNS で見られる部分**（Gmail・Yahoo 2024-02、
    # Microsoft 2025-05）。SPF と DKIM と DMARC（p=none でよい）がそろって
    # いるか。整合（alignment）・PTR・TLS・配信停止・苦情率は DNS では見えない
    bulk_spf = [r for r in sending if _spf_usable(r)]
    bulk_dkim = [r for r in sending if r.dkim_status == DkimStatus.DETECTED]
    bulk_dmarc = [r for r in sending if _dmarc_usable(r)]
    bulk_ready = [
        r for r in sending
        if _spf_usable(r) and r.dkim_status == DkimStatus.DETECTED and _dmarc_usable(r)
    ]

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
        # 2つある・p= が無いか不正。**受け手はこのレコードを使わない**
        # （RFC 7489 §6.6.3。p= が不正でも rua が正しければ none 扱いにする
        # 受け手もあるが、どちらにしても強制はしていない）
        dmarc_invalid_domains=sum(
            1
            for r in observed
            if r.dmarc_present
            and (r.dmarc_multiple_records or r.dmarc_p not in _VALID_DMARC_P)
        ),
        # **集計レポートを受け取る宛先が無い。** 何が届いて何が落ちているかが
        # 分からない。p=none（観察する方針）でこれだと、観察もしていない
        dmarc_no_rua_domains=sum(
            1 for r in observed if r.dmarc_present and not r.dmarc_has_rua
        ),
        # rua の宛先が雛形のまま（例示用に予約されたドメイン）。レポートは誰にも届かない
        rua_placeholder_domains=sum(
            1 for r in observed if r.dmarc_present and r.rua_placeholder
        ),
        # **p=reject で、集計レポートも受け取っている**（EasyDMARC の報告の基準）
        reject_with_rua_domains=sum(
            1
            for r in observed
            if r.dmarc_present and not r.dmarc_multiple_records and r.dmarc_p == "reject"
            and r.dmarc_has_rua and not r.rua_placeholder
        ),
        dmarc_none_no_rua_domains=sum(
            1
            for r in observed
            if r.dmarc_present and not r.dmarc_has_rua and r.dmarc_p == "none"
        ),
        # 名目は「そう書いてある」、実効は「それが効いている」
        nominal_reject_domains=sum(1 for r in observed if r.dmarc_p == "reject"),
        enforced_reject_domains=sum(
            1 for r in observed if r.policy_label == PolicyLabel.ENFORCED_REJECT
        ),
        blind_reject_domains=sum(
            1 for r in observed if r.policy_label == PolicyLabel.BLIND_REJECT
        ),
        # 自分と配下を分ける。差が出るのは sp= を明示して下げている場合だけ
        sp_enforced_domains=sum(1 for r in observed if r.subdomain_enforced),
        sp_weaker_domains=sum(1 for r in observed if r.sp_weaker),
        # **実際に引いたサブドメイン。** 上のどの分母にも入っていない
        subdomains_measured=len(subdomains),
        subdomains_observed=len(subdomains_observed),
        subdomains_with_own_dmarc=sum(1 for r in subdomains_observed if r.dmarc_present),
        # **継承したものも数える。** 自分の `_dmarc` を持たないサブドメインは
        # 上位の `sp=` が効いている。自分のレコードだけ見ると、守られている
        # のに未対応側に入る（BACKLOG 14 の d）
        subdomains_dmarc_enforced=sum(
            1 for r in subdomains_observed if r.applied_enforced
        ),
        subdomains_inherited_policy=sum(
            1 for r in subdomains_observed if not r.dmarc_present and r.applied_7489
        ),
        entities_with_subdomain_mail=len(
            {r.entity_id for r in subdomains if r.entity_id}
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
        dkim_key_short_domains=sum(1 for b in dkim_keys if b < 1024),
        dkim_key_1024_domains=sum(1 for b in dkim_keys if 1024 <= b < 2048),
        dkim_key_2048_domains=sum(1 for b in dkim_keys if b >= 2048),
        mta_sts_domains=sum(1 for r in observed if r.mta_sts_present),
        # **TXT があるだけでは効いていない。** ポリシーを取って mode を見る
        # （P5 `mta_sts_policy.py`）。取っていない run では 0 ではなく null
        mta_sts_enforce_domains=(
            sum(1 for r in observed if r.mta_sts_present and r.mta_sts_mode == "enforce")
            if policy_fetched
            else None
        ),
        mta_sts_testing_domains=(
            sum(1 for r in observed if r.mta_sts_present and r.mta_sts_mode == "testing")
            if policy_fetched
            else None
        ),
        mta_sts_unusable_domains=(
            sum(
                1
                for r in observed
                if r.mta_sts_present and r.mta_sts_policy_status not in (None, "ok")
            )
            if policy_fetched
            else None
        ),
        mta_sts_mx_mismatch_domains=(
            sum(1 for r in observed if r.mta_sts_mx_mismatch) if policy_fetched else None
        ),
        tls_rpt_domains=sum(1 for r in observed if r.tls_rpt_present),
        # **rua の外部宛先の認可**（RFC 7489 §7.1 / RFC 9990）。宛先のドメインが
        # `<自ドメイン>._report._dmarc.<宛先>` を公開していないと、受け手は
        # レポートを送らないことがある。**引けなかったもの（None）は数えない**
        rua_external_domains=sum(
            1 for r in observed if r.dmarc_present and r.rua_external
        ),
        rua_unauthorized_domains=sum(
            1
            for r in observed
            if r.dmarc_present and r.rua_external and r.rua_authorized is False
        ),
        rua_unregistered_domains=sum(
            1
            for r in observed
            if r.dmarc_present and r.rua_external and r.rua_domain_unregistered is True
        ),
        # **include の先まで数える**（RFC 7208 §4.6.4 の上限は評価の全体で1つ）。
        # たどれなかったもの（None）は数えない
        spf_lookup_exceeded_domains=(
            sum(1 for r in observed if r.spf_present and r.spf_total_exceeds is True)
            if spf_walked
            else None
        ),
        spf_target_broken_domains=(
            sum(1 for r in observed if r.spf_present and r.spf_target_broken is True)
            if spf_walked
            else None
        ),
        # BACKLOG 49。**列の無い過去の run では 0 ではなく null**
        spf_dangling_domains=_count_true(
            observed, lambda r: r.spf_present, lambda r: r.spf_target_unregistered
        ),
        txt_apex_oversize_domains=(
            sum(1 for r in observed
                if r.txt_apex_bytes is not None and r.txt_apex_bytes > TXT_UDP_SAFE_BYTES)
            if any(r.txt_apex_bytes is not None for r in observed)
            else None
        ),
        dkim_ed25519_only_domains=_count_true(
            observed, lambda r: True, lambda r: r.dkim_ed25519_only
        ),
        # 試したセレクタでは見つからないが、_domainkey の配下に名前がある。
        # **「DKIM が無い」と読まれがちな数のうち、鍵がありそうなもの**
        dkim_other_selector_domains=_count_true(
            observed,
            lambda r: r.dkim_status == DkimStatus.NOT_FOUND_IN_KNOWN_SELECTORS,
            lambda r: r.dkim_domainkey_exists,
        ),
        dmarc_historic_tags_domains=_count_true(
            observed, lambda r: r.dmarc_present, lambda r: r.dmarc_historic_tags
        ),
        spf_ip4_broad_domains=(
            sum(1 for r in observed if r.spf_present and (r.spf_ip4_addresses or 0) > SPF_IP4_BROAD)
            if any(r.spf_ip4_addresses is not None for r in observed)
            else None
        ),
        spf_ptr_domains=_count_true(observed, lambda r: r.spf_present, lambda r: r.spf_has_ptr),
        dmarc_ruf_domains=sum(1 for r in observed if r.dmarc_present and r.dmarc_has_ruf),
        spf_all_fail_domains=all_q.get("-", 0),
        spf_all_softfail_domains=all_q.get("~", 0),
        spf_all_neutral_domains=all_q.get("?", 0),
        spf_all_pass_domains=all_q.get("+", 0),
        spf_all_missing_domains=all_q.get(None, 0),
        spf_all_unknown_domains=sum(1 for r in spf_single if r.spf_all_unknown),
        bimi_domains=sum(1 for r in observed if r.bimi_present),
        bimi_vmc_domains=sum(1 for r in observed if r.bimi_present and r.bimi_has_vmc),
        dnssec_domains=sum(1 for r in observed if r.dnssec_signed),
        maturity_stage_dist=json.dumps(
            {str(k): stages.get(k, 0) for k in range(5)}, sort_keys=True
        ),
        sending_domains=len(sending),
        # RFC 9989 で入ったタグ。値が正しいものだけ数える
        dmarc_np_domains=sum(
            1 for r in observed if r.dmarc_present and (r.dmarc_np or "") in _VALID_DMARC_P
        ),
        dmarc_testing_domains=sum(
            1 for r in observed if r.dmarc_present and (r.dmarc_t or "").lower() == "y"
        ),
        evidence_signed_entities=evidence["signed"],
        evidence_sending_entities=evidence["sending"],
        evidence_dkim_hint_entities=(
            evidence["sending_dkim_hint"]
            if any(r.dkim_domainkey_exists is not None for r in observed)
            else None
        ),
        evidence_receiving_entities=evidence["receiving"],
        evidence_none_entities=evidence["none"],
        mail_off_site_entities=evidence["off_site"],
        ownership_evidence_dist=ownership_evidence(observed),
        bulk_spf_ok_domains=len(bulk_spf),
        bulk_dkim_domains=len(bulk_dkim),
        bulk_dmarc_ok_domains=len(bulk_dmarc),
        bulk_ready_domains=len(bulk_ready),
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
