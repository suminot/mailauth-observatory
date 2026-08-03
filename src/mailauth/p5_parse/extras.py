"""周辺プロトコルの解釈（BIMI / MTA-STS / TLS-RPT / DNSSEC / DANE）。

いずれも限界費用ゼロで併測できる（DR-15）。DMARC を事実上の前提とするため、
成熟度ステージでは順序性を持たせて扱う（P7）。単純加算すると下位項目を
二重評価してしまう。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..records import dmarc_report_domains


@dataclass
class MtaStsResult:
    present: bool = False
    id: str | None = None
    #: mode は DNS からは分からない。HTTPS でポリシーを取らないと確定しない
    mode: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class TlsRptResult:
    present: bool = False
    rua: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class BimiResult:
    present: bool = False
    has_svg: bool = False
    #: VMC（Verified Mark Certificate）の有無。a= タグで示される
    has_vmc: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass
class DaneResult:
    present: bool = False
    tlsa_count: int = 0
    #: DNSSEC で署名されていなければ DANE は実効しない。
    #: 「TLSA はあるが親ゾーンが未署名」という誤設定を検出できる
    dnssec_signed: bool | None = None
    orphan: bool = False
    notes: list[str] = field(default_factory=list)


def _tags(txt: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in txt.split(";"):
        name, sep, value = part.partition("=")
        if sep:
            key = name.strip().lower()
            if key and key not in out:
                out[key] = value.strip()
    return out


def parse_mta_sts(txt_records: list[str]) -> MtaStsResult:
    """_mta-sts の TXT。`v=STSv1; id=...` 形式。"""
    for txt in txt_records:
        tags = _tags(txt)
        if (tags.get("v") or "").upper() == "STSV1":
            result = MtaStsResult(present=True, id=tags.get("id"))
            if not result.id:
                result.notes.append("id タグが無い。ポリシーの更新検知ができない")
            result.notes.append(
                "mode は DNS では分からない。enforce か testing かは "
                "HTTPS でポリシーを取得して確認する（第2段）"
            )
            return result
    return MtaStsResult(present=False)


def parse_tls_rpt(txt_records: list[str]) -> TlsRptResult:
    """_smtp._tls の TXT。`v=TLSRPTv1; rua=mailto:...` 形式。"""
    for txt in txt_records:
        tags = _tags(txt)
        if (tags.get("v") or "").upper() == "TLSRPTV1":
            # rua は mailto: か https: を取りうる。ドメインだけ保存する
            rua = dmarc_report_domains(txt, "rua")
            result = TlsRptResult(present=True, rua=rua)
            if not rua and tags.get("rua"):
                result.notes.append("rua が https: 形式のためドメインを抽出していない")
            return result
    return TlsRptResult(present=False)


def parse_bimi(txt_records: list[str]) -> BimiResult:
    """default._bimi の TXT。`v=BIMI1; l=<svg url>; a=<vmc url>` 形式。"""
    for txt in txt_records:
        tags = _tags(txt)
        if (tags.get("v") or "").upper() == "BIMI1":
            location = tags.get("l") or ""
            authority = tags.get("a") or ""
            result = BimiResult(
                present=True,
                has_svg=bool(location.strip()),
                has_vmc=bool(authority.strip()),
            )
            if not result.has_svg:
                result.notes.append("l タグが空。ロゴが指定されていない（宣言のみ）")
            if not result.has_vmc:
                result.notes.append(
                    "a タグが無い。VMC を伴わない BIMI は主要受信者では表示されない"
                )
            result.notes.append("SVG と VMC の実体検証は HTTPS 取得が必要（第2段）")
            return result
    return BimiResult(present=False)


def parse_dane(
    tlsa_records: list[str], *, dnssec_signed: bool | None = None
) -> DaneResult:
    """_25._tcp.<mx-host> の TLSA。

    **DNSSEC 署名の有無を必ず併記する。** DANE は DNSSEC が前提のため、
    「TLSA はあるが親ゾーンが未署名で実効しない」という誤設定を検出できる。
    これも他があまり出していない数字である（DESIGN.md P4）。
    """
    present = len(tlsa_records) > 0
    result = DaneResult(
        present=present,
        tlsa_count=len(tlsa_records),
        dnssec_signed=dnssec_signed,
    )
    if present and dnssec_signed is False:
        result.orphan = True
        result.notes.append(
            "TLSA はあるが DNSSEC 署名が確認できない。DANE は実効しない（誤設定）"
        )
    if present and dnssec_signed is None:
        result.notes.append("DNSSEC の署名状態が不明。DANE の実効性を判定できない")
    return result


def maturity_stage(
    *,
    spf_present: bool,
    dkim_detected: bool,
    dmarc_present: bool,
    dmarc_enforced: bool,
    mta_sts_present: bool,
    tls_rpt_present: bool,
    dnssec_signed: bool,
    bimi_with_vmc: bool,
    dane_present: bool,
) -> int:
    """成熟度ステージ（DESIGN.md P7）。

    MTA-STS / BIMI / DANE はいずれも DMARC を事実上の前提とするため、
    単純加算すると下位項目を二重評価する。順序性を反映した階層で表す。

      Stage 0: SPFのみ / レコードなし
      Stage 1: SPF + DKIM(既知セレクタで検出) + DMARC p=none
      Stage 2: DMARC p=quarantine/reject（強制ポリシー）
      Stage 3: Stage 2 + (MTA-STS enforce または TLS-RPT) + DNSSEC署名
      Stage 4: Stage 3 + BIMI(有効SVG+VMC) または DANE/TLSA
    """
    if not (spf_present and dkim_detected and dmarc_present):
        return 0
    if not dmarc_enforced:
        return 1
    if not ((mta_sts_present or tls_rpt_present) and dnssec_signed):
        return 2
    if not (bimi_with_vmc or dane_present):
        return 3
    return 4
