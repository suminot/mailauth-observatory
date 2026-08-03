"""全フェーズの入出力スキーマ（DESIGN.md 第6章）。

ここが唯一のスキーマ定義。各フェーズはこのモジュールのモデルだけを介して
やり取りし、互いの実装を知らない（原則3）。

Pydantic モデルと PyArrow スキーマの両方を定義しているのは理由がある。
Pydantic は1レコードの検証、PyArrow は Parquet 書き出し時の型固定に使う。
後者を明示しておかないと、ある月に全件 NULL になった列の型が推論で揺れ、
月をまたいだ結合が壊れる。
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

import pyarrow as pa
from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"


# --------------------------------------------------------------------------
# 列挙
# --------------------------------------------------------------------------


class EntityStatus(StrEnum):
    ACTIVE = "active"
    DELISTED = "delisted"
    MERGED = "merged"
    RENAMED = "renamed"


class DiscoveryMethod(StrEnum):
    OFFICIAL_URL = "official_url"
    CT_LOG = "ct_log"
    SPF_REDIRECT = "spf_redirect"
    DMARC_RUA = "dmarc_rua"
    MANUAL = "manual"


class DomainRole(StrEnum):
    PRIMARY = "primary"
    RELATED = "related"
    PARKED = "parked"


class Confidence(StrEnum):
    """P3 の確度フラグ。parked は「送信していないことが明示されている」状態。"""

    CONFIRMED = "confirmed"
    LIKELY = "likely"
    UNKNOWN = "unknown"
    PARKED = "parked"


class MeasureTier(StrEnum):
    """P4 の計測の深さ（DESIGN.md P2「ドメイン階層の割り当て」）。"""

    A = "A"  # フル計測。DKIM 50セレクタ、DANE を含む
    C = "C"  # 簡易計測。MX / SPF / DMARC / Null MX のみ


class QueryPurpose(StrEnum):
    MX = "mx"
    SPF = "spf"
    DMARC = "dmarc"
    DMARC_SUBDOMAIN = "dmarc_subdomain"
    DKIM = "dkim"
    DKIM_CONTROL = "dkim_control"
    DKIM_WILDCARD = "dkim_wildcard"
    MTA_STS = "mta_sts"
    TLS_RPT = "tls_rpt"
    BIMI = "bimi"
    DANE = "dane"
    NS = "ns"


class DkimStatus(StrEnum):
    """三値表現（原則5）。「未設定」と「既知セレクタでは未検出」を混同しない。"""

    DETECTED = "detected"
    NOT_FOUND_IN_KNOWN_SELECTORS = "not_found_in_known_selectors"
    NOT_APPLICABLE = "not_applicable"


class SpecVersion(StrEnum):
    RFC7489 = "rfc7489"
    RFC9989 = "rfc9989"


class PolicyLabel(StrEnum):
    """DESIGN.md P5「ポリシー強度の分類ラベル」。"""

    ENFORCED_REJECT = "enforced_reject"
    NOMINAL_REJECT_WEAK_PCT = "nominal_reject_weak_pct"
    NOMINAL_REJECT_TESTING = "nominal_reject_testing"
    BLIND_REJECT = "blind_reject"
    NOMINAL_QUARANTINE_WEAK_PCT = "nominal_quarantine_weak_pct"
    MONITORING = "monitoring"
    INEFFECTIVE = "ineffective"
    NONE = "none"


class InferenceCategory(StrEnum):
    MAIL_PLATFORM = "mail_platform"
    SECURITY_GATEWAY = "security_gateway"
    ESP = "esp"
    DMARC_VENDOR = "dmarc_vendor"


class ConfidenceLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ParkClass(StrEnum):
    """パークドメイン分類（DESIGN.md P6）。本システム固有の差別化指標。"""

    HARDENED_PARKED = "hardened_parked"
    DEFENDED_PARKED = "defended_parked"
    INTENTIONAL_NO_SEND = "intentional_no_send"
    NEGLECTED = "neglected"
    ACTIVE_SENDING = "active_sending"
    INCONSISTENT = "inconsistent"


class EvidenceRecordType(StrEnum):
    """証拠の種別。強さの順は DKIM_CNAME >= MX > SPF_INCLUDE > VERIFICATION_TXT。"""

    DKIM_CNAME = "DKIM_CNAME"
    MX = "MX"
    SPF_INCLUDE = "SPF_INCLUDE"
    SPF_MECHANISM = "SPF_MECHANISM"
    VERIFICATION_TXT = "VERIFICATION_TXT"
    TXT = "TXT"
    DMARC_RUA_DOMAIN = "DMARC_RUA_DOMAIN"


#: 推定に用いる証拠の強さ（DESIGN.md P6「二段推定」）
INFERENCE_PRIORITY: dict[str, int] = {
    EvidenceRecordType.DKIM_CNAME: 4,
    EvidenceRecordType.MX: 3,
    EvidenceRecordType.SPF_INCLUDE: 2,
    EvidenceRecordType.SPF_MECHANISM: 2,
    EvidenceRecordType.VERIFICATION_TXT: 1,
}


class _Model(BaseModel):
    model_config = ConfigDict(use_enum_values=True, extra="forbid", str_strip_whitespace=True)


class _RawModel(BaseModel):
    """bronze（生データ）用。**空白を一切削らない**。

    原則1 は生データの忠実性を要求する。TXT の character-string は
    前後の空白まで意味を持つ。たとえば 255 バイト境界で分割された SPF が

        ["v=spf1 include:_spf.example.com ", "-all"]

    のとき、末尾の空白を削ると連結結果が "…example.com-all" になり、
    SPF レコードとして成立しなくなる。`str_strip_whitespace` を
    生データに適用してはいけない。
    """

    model_config = ConfigDict(use_enum_values=True, extra="forbid", str_strip_whitespace=False)


# --------------------------------------------------------------------------
# P1 母集団確定
# --------------------------------------------------------------------------


class Entity(_Model):
    """企業1社。entity_id は将来 LEI ベースへ移行できるよう接頭辞付きにする。

    population_ids は Sprint 1 時点でも必ず配列で持つ。Sprint 1.5 で
    global500 と重複排除するとき、1社が複数母集団に属するため。
    """

    entity_id: str
    run_id: str
    country: str
    population_ids: list[str] = Field(default_factory=list)
    is_duplicate_of: str | None = None

    name: str
    name_en: str | None = None
    name_normalized: str

    houjin_bangou: str | None = None
    edinet_code: str | None = None
    securities_code: str | None = None
    cik: str | None = None
    lei: str | None = None
    ticker: str | None = None

    official_url: str | None = None
    official_domain: str | None = None

    industry_scheme: str | None = None
    industry_code: str | None = None
    industry_label: str | None = None
    common12_code: str | None = None
    common12_label: str | None = None
    industry_map_version: str | None = None

    #: 市場区分（prime / standard / growth など）。
    #: **内部の集計軸専用**であり、公開成果物には出さない（DESIGN.md P1）。
    #: JPX 由来のファイルは使わないため、区分は別途与えた対応表からのみ付く。
    #: 与えていなければ None。「区分が無い」ではなく「区分を知らない」を意味する。
    market_segment: str | None = None
    market_segment_source: str | None = None

    first_seen_month: dt.date | None = None
    last_seen_month: dt.date | None = None
    status: str = EntityStatus.ACTIVE
    change_note: str | None = None


ENTITY_ARROW_SCHEMA = pa.schema(
    [
        ("entity_id", pa.string()),
        ("run_id", pa.string()),
        ("country", pa.string()),
        ("population_ids", pa.list_(pa.string())),
        ("is_duplicate_of", pa.string()),
        ("name", pa.string()),
        ("name_en", pa.string()),
        ("name_normalized", pa.string()),
        ("houjin_bangou", pa.string()),
        ("edinet_code", pa.string()),
        ("securities_code", pa.string()),
        ("cik", pa.string()),
        ("lei", pa.string()),
        ("ticker", pa.string()),
        ("official_url", pa.string()),
        ("official_domain", pa.string()),
        ("industry_scheme", pa.string()),
        ("industry_code", pa.string()),
        ("industry_label", pa.string()),
        ("common12_code", pa.string()),
        ("common12_label", pa.string()),
        ("industry_map_version", pa.string()),
        ("market_segment", pa.string()),
        ("market_segment_source", pa.string()),
        ("first_seen_month", pa.date32()),
        ("last_seen_month", pa.date32()),
        ("status", pa.string()),
        ("change_note", pa.string()),
    ]
)

#: 冪等性のためのソート順（原則6）。同じ入力なら必ず同じ行順になる。
ENTITY_SORT_KEYS = ["entity_id"]


# --------------------------------------------------------------------------
# P2 ドメイン候補生成
# --------------------------------------------------------------------------


class DomainCandidate(_Model):
    candidate_id: str
    entity_id: str
    run_id: str
    domain: str
    discovery_method: str
    discovered_at: dt.datetime
    source_detail: str | None = None
    is_apex: bool = True


DOMAIN_CANDIDATE_ARROW_SCHEMA = pa.schema(
    [
        ("candidate_id", pa.string()),
        ("entity_id", pa.string()),
        ("run_id", pa.string()),
        ("domain", pa.string()),
        ("discovery_method", pa.string()),
        ("discovered_at", pa.timestamp("us", tz="UTC")),
        ("source_detail", pa.string()),
        ("is_apex", pa.bool_()),
    ]
)

DOMAIN_CANDIDATE_SORT_KEYS = ["entity_id", "domain", "discovery_method"]


# --------------------------------------------------------------------------
# P3 メールドメイン確定
# --------------------------------------------------------------------------


class Domain(_Model):
    domain_id: str
    entity_id: str
    run_id: str
    domain: str
    domain_role: str
    confidence: str

    mx_exists: bool | None = None
    spf_exists: bool | None = None
    spf_aligned: bool | None = None
    dmarc_exists: bool | None = None
    dkim_found: bool | None = None
    evidence_count: int = 0
    evidence: str | None = None  # JSON 文字列

    is_measured: bool = False
    measure_tier: str | None = None
    exclusion_reason: str | None = None

    # パークドメイン判定の材料（P6 で使う）
    null_mx: bool | None = None
    spf_hard_deny: bool | None = None


DOMAIN_ARROW_SCHEMA = pa.schema(
    [
        ("domain_id", pa.string()),
        ("entity_id", pa.string()),
        ("run_id", pa.string()),
        ("domain", pa.string()),
        ("domain_role", pa.string()),
        ("confidence", pa.string()),
        ("mx_exists", pa.bool_()),
        ("spf_exists", pa.bool_()),
        ("spf_aligned", pa.bool_()),
        ("dmarc_exists", pa.bool_()),
        ("dkim_found", pa.bool_()),
        ("evidence_count", pa.int32()),
        ("evidence", pa.string()),
        ("is_measured", pa.bool_()),
        ("measure_tier", pa.string()),
        ("exclusion_reason", pa.string()),
        ("null_mx", pa.bool_()),
        ("spf_hard_deny", pa.bool_()),
    ]
)

DOMAIN_SORT_KEYS = ["entity_id", "domain"]


# --------------------------------------------------------------------------
# P4 DNS計測（bronze / JSON Lines）
# --------------------------------------------------------------------------


class DnsAnswer(_RawModel):
    type: str
    ttl: int | None = None
    #: 分割された TXT は連結せず character-string の配列のまま保存する。
    #: 連結は P5 の責務（DESIGN.md P4）。
    data: str | list[str]


class DnssecFlags(_RawModel):
    do: bool | None = None
    ad: bool | None = None
    rrsig_present: bool | None = None


class RawResponse(_RawModel):
    """bronze の1行。ここに書いたものは以後一切変更しない（原則1）。"""

    schema_version: str = SCHEMA_VERSION
    run_id: str
    ts: dt.datetime
    domain: str
    query_name: str
    query_type: str
    purpose: str
    method: str
    resolver: str
    protocol: str
    rcode: str

    #: 原則5。observed はクエリ自体が成功したか、
    #: record_present はレコードが存在したか。両者は別の問い。
    observed: bool
    record_present: bool | None = None

    answers: list[DnsAnswer] = Field(default_factory=list)
    #: 辿った CNAME の先。求めた型の rrset ではないので answers には入れないが、
    #: DKIM セレクタの委譲先はここにしか現れない（DESIGN.md P6 二段推定）
    cname_chain: list[str] = Field(default_factory=list)
    authorities: list[dict] = Field(default_factory=list)
    additionals: list[dict] = Field(default_factory=list)
    authoritative_ns: str | None = None
    dnssec: DnssecFlags | None = None

    retries: int = 0
    duration_ms: int | None = None
    tool: str | None = None
    tool_version: str | None = None
    error: str | None = None


# --------------------------------------------------------------------------
# P5 パース（silver / facts.parquet）
# --------------------------------------------------------------------------


class Fact(_Model):
    """DNS から直接読み取れる値のみ。推察は入れない（原則2）。"""

    fact_id: str
    domain_id: str
    entity_id: str
    run_id: str
    measured_month: dt.date

    observed: bool
    record_present: bool | None = None

    # 生値（原則1の延長。silver でも生を捨てない）
    raw_spf: str | None = None
    raw_dmarc: str | None = None
    raw_mx: str | None = None

    # SPF
    spf_present: bool | None = None
    spf_valid: bool | None = None
    spf_error: str | None = None
    spf_all_qualifier: str | None = None
    spf_lookup_count: int | None = None
    spf_void_count: int | None = None
    spf_exceeds_limit: bool | None = None
    spf_includes: list[str] = Field(default_factory=list)
    #: include 以外のメカニズムも保持する。さくらインターネットのように
    #: 専用 include を持たず `a:wwwNNNN.sakura.ne.jp` で表現する事業者があり、
    #: include だけ見ていると取りこぼす（DESIGN.md P6 実装メモ）。
    #: ip4/ip6/all は数が多く照合にも使わないので除く
    spf_mechanisms: list[str] = Field(default_factory=list)
    spf_is_flattened: bool | None = None
    spf_is_dynamic: bool | None = None

    # DMARC
    dmarc_present: bool | None = None
    dmarc_p: str | None = None
    dmarc_sp: str | None = None
    dmarc_np: str | None = None
    dmarc_pct: int | None = None
    dmarc_t: str | None = None
    dmarc_psd: str | None = None
    dmarc_adkim: str | None = None
    dmarc_aspf: str | None = None
    dmarc_rua: list[str] = Field(default_factory=list)
    dmarc_ruf: list[str] = Field(default_factory=list)
    dmarc_has_duplicate_tag: bool | None = None
    dmarc_multiple_records: bool | None = None

    effective_7489: str | None = None
    effective_9989: str | None = None
    policy_label: str | None = None
    blind_enforcement: bool | None = None

    org_domain_psl: str | None = None
    org_domain_treewalk: str | None = None
    org_domain_divergence: bool | None = None

    rua_external: bool | None = None
    rua_authorized: bool | None = None
    rua_domain_unregistered: bool | None = None

    # DKIM（三値表現。原則5の適用）
    dkim_status: str | None = None
    dkim_selectors: list[str] = Field(default_factory=list)
    dkim_key_bits: list[int] = Field(default_factory=list)
    dkim_testing_flag: bool | None = None
    dkim_revoked: bool | None = None
    dkim_wildcard_suspect: bool | None = None
    #: セレクタの委譲先。ゲートウェイが MX を握っていても署名基盤は
    #: ここに出る。推定証拠として最も強い（DESIGN.md P6）
    dkim_cname_targets: list[str] = Field(default_factory=list)
    #: `*._domainkey` に失効鍵が置かれていた（M3AAWG のパーク推奨構成）
    dkim_wildcard_revoked: bool | None = None

    # MX
    mx_present: bool | None = None
    mx_hosts: list[str] = Field(default_factory=list)
    #: RFC 7505 の Null MX（`0 .`）。**「MX が無い」とは別の事実**である。
    #: mx_present は Null MX を False にするので、区別にはこの列が必要
    mx_null: bool | None = None

    #: apex TXT のうち SPF 以外。所有権確認 TXT の照合に使う。
    #: DKIM 鍵や DMARC は別の名前にあるのでここには入らない
    verification_txt: list[str] = Field(default_factory=list)

    # 周辺プロトコル
    mta_sts_present: bool | None = None
    mta_sts_id: str | None = None
    mta_sts_mode: str | None = None
    tls_rpt_present: bool | None = None
    tls_rpt_rua: list[str] = Field(default_factory=list)
    bimi_present: bool | None = None
    bimi_has_vmc: bool | None = None
    dnssec_signed: bool | None = None
    dane_present: bool | None = None

    spec_version: str | None = None
    parser_version: str | None = None


FACT_ARROW_SCHEMA = pa.schema(
    [
        ("fact_id", pa.string()),
        ("domain_id", pa.string()),
        ("entity_id", pa.string()),
        ("run_id", pa.string()),
        ("measured_month", pa.date32()),
        ("observed", pa.bool_()),
        ("record_present", pa.bool_()),
        ("raw_spf", pa.string()),
        ("raw_dmarc", pa.string()),
        ("raw_mx", pa.string()),
        ("spf_present", pa.bool_()),
        ("spf_valid", pa.bool_()),
        ("spf_error", pa.string()),
        ("spf_all_qualifier", pa.string()),
        ("spf_lookup_count", pa.int32()),
        ("spf_void_count", pa.int32()),
        ("spf_exceeds_limit", pa.bool_()),
        ("spf_includes", pa.list_(pa.string())),
        ("spf_mechanisms", pa.list_(pa.string())),
        ("spf_is_flattened", pa.bool_()),
        ("spf_is_dynamic", pa.bool_()),
        ("dmarc_present", pa.bool_()),
        ("dmarc_p", pa.string()),
        ("dmarc_sp", pa.string()),
        ("dmarc_np", pa.string()),
        ("dmarc_pct", pa.int32()),
        ("dmarc_t", pa.string()),
        ("dmarc_psd", pa.string()),
        ("dmarc_adkim", pa.string()),
        ("dmarc_aspf", pa.string()),
        ("dmarc_rua", pa.list_(pa.string())),
        ("dmarc_ruf", pa.list_(pa.string())),
        ("dmarc_has_duplicate_tag", pa.bool_()),
        ("dmarc_multiple_records", pa.bool_()),
        ("effective_7489", pa.string()),
        ("effective_9989", pa.string()),
        ("policy_label", pa.string()),
        ("blind_enforcement", pa.bool_()),
        ("org_domain_psl", pa.string()),
        ("org_domain_treewalk", pa.string()),
        ("org_domain_divergence", pa.bool_()),
        ("rua_external", pa.bool_()),
        ("rua_authorized", pa.bool_()),
        ("rua_domain_unregistered", pa.bool_()),
        ("dkim_status", pa.string()),
        ("dkim_selectors", pa.list_(pa.string())),
        ("dkim_key_bits", pa.list_(pa.int32())),
        ("dkim_testing_flag", pa.bool_()),
        ("dkim_revoked", pa.bool_()),
        ("dkim_wildcard_suspect", pa.bool_()),
        ("dkim_cname_targets", pa.list_(pa.string())),
        ("dkim_wildcard_revoked", pa.bool_()),
        ("mx_present", pa.bool_()),
        ("mx_hosts", pa.list_(pa.string())),
        ("mx_null", pa.bool_()),
        ("verification_txt", pa.list_(pa.string())),
        ("mta_sts_present", pa.bool_()),
        ("mta_sts_id", pa.string()),
        ("mta_sts_mode", pa.string()),
        ("tls_rpt_present", pa.bool_()),
        ("tls_rpt_rua", pa.list_(pa.string())),
        ("bimi_present", pa.bool_()),
        ("bimi_has_vmc", pa.bool_()),
        ("dnssec_signed", pa.bool_()),
        ("dane_present", pa.bool_()),
        ("spec_version", pa.string()),
        ("parser_version", pa.string()),
    ]
)

FACT_SORT_KEYS = ["domain_id"]


# --------------------------------------------------------------------------
# P6 推察（silver / inferences.parquet）
# --------------------------------------------------------------------------


class Evidence(_Model):
    """推察の根拠。fact への参照を必ず持つ（原則2）。"""

    record_type: str
    matched_value: str
    rule_id: str


class Inference(_Model):
    """fact から導いた推定。confidence と evidence を必ず伴う。"""

    inference_id: str
    domain_id: str
    entity_id: str
    run_id: str
    measured_month: dt.date

    category: str
    vendor: str
    product: str | None = None
    confidence: str
    is_stale: bool = False
    #: 所有権確認 TXT の裏付けが取れなかった連続月数。
    #: 3か月連続で is_stale に降格する（DESIGN.md P6）。
    #: 判定対象でない推定では null
    stale_streak_months: int | None = None

    evidence: str | None = None  # JSON 文字列
    rule_ids: list[str] = Field(default_factory=list)
    fingerprint_version: str | None = None
    note: str | None = None

    park_class: str | None = None
    park_has_null_mx: bool | None = None
    park_has_wildcard_dkim_revoked: bool | None = None

    #: DNS に痕跡を残さない製品があるため、「検出されなかった＝使っていない」
    #: ではないことを明示する（DESIGN.md P6）。
    undetectable_reason: str | None = None


INFERENCE_ARROW_SCHEMA = pa.schema(
    [
        ("inference_id", pa.string()),
        ("domain_id", pa.string()),
        ("entity_id", pa.string()),
        ("run_id", pa.string()),
        ("measured_month", pa.date32()),
        ("category", pa.string()),
        ("vendor", pa.string()),
        ("product", pa.string()),
        ("confidence", pa.string()),
        ("is_stale", pa.bool_()),
        ("stale_streak_months", pa.int32()),
        ("evidence", pa.string()),
        ("rule_ids", pa.list_(pa.string())),
        ("fingerprint_version", pa.string()),
        ("note", pa.string()),
        ("park_class", pa.string()),
        ("park_has_null_mx", pa.bool_()),
        ("park_has_wildcard_dkim_revoked", pa.bool_()),
        ("undetectable_reason", pa.string()),
    ]
)

#: rule_ids は list 列なのでソートキーにできない。
#: (domain_id, category, vendor) が主キー相当で、inference_id はその
#: ハッシュなので最後の同値解消に足りる
INFERENCE_SORT_KEYS = ["domain_id", "category", "vendor", "inference_id"]


# --------------------------------------------------------------------------
# P7 集計（gold）
# --------------------------------------------------------------------------


class StatsOverall(_Model):
    measured_month: dt.date
    population_id: str
    total_entities: int
    total_domains: int

    # 企業数ベースとドメインベースの両方を必ず出す（DESIGN.md P7 受け入れ基準）
    spf_adopted_entities: int = 0
    spf_adopted_domains: int = 0
    dmarc_adopted_entities: int = 0
    dmarc_adopted_domains: int = 0
    dmarc_enforced_entities: int = 0
    dmarc_enforced_domains: int = 0

    # 名目と実効を分ける
    nominal_reject_domains: int = 0
    enforced_reject_domains: int = 0
    blind_reject_domains: int = 0

    dkim_detected_domains: int = 0
    dkim_not_found_domains: int = 0
    mta_sts_domains: int = 0
    tls_rpt_domains: int = 0
    bimi_domains: int = 0
    dnssec_domains: int = 0

    maturity_stage_dist: str | None = None  # JSON 文字列

    # パークドメイン指標（本システム固有）
    sending_domains: int = 0
    sending_enforced: int = 0
    parked_domains: int = 0
    parked_hardened: int = 0
    parked_defended: int = 0
    parked_intentional: int = 0
    parked_neglected: int = 0
    park_defense_rate: float | None = None

    # 地域別クロス集計の軸
    dane_domains: int = 0
    dane_dnssec_valid: int = 0
    dane_orphan: int = 0

    delta_prev_month: str | None = None  # JSON 文字列
    spec_version: str | None = None


class StatsBySector(StatsOverall):
    common12_code: str
    common12_label: str
    n_entities: int
    #: n<5 でセル秘匿した（DESIGN.md P7「セル秘匿」）
    suppressed: bool = False


#: 業種別集計の公開閾値。これを下回るセルは「その他」に統合するか非公開にする。
MIN_CELL_SIZE = 5


# --------------------------------------------------------------------------
# 成熟度ステージ（DESIGN.md P7）
# --------------------------------------------------------------------------

MATURITY_STAGES = {
    0: "SPFのみ / レコードなし",
    1: "SPF + DKIM(既知セレクタで検出) + DMARC p=none",
    2: "DMARC p=quarantine/reject（強制ポリシー）",
    3: "Stage 2 + (MTA-STS enforce または TLS-RPT) + DNSSEC署名",
    4: "Stage 3 + BIMI(有効SVG+VMC) または DANE/TLSA",
}
