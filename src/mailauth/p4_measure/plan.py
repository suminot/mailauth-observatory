"""計測するクエリの組み立て（DESIGN.md P4「取得するレコード（階層別）」）。

階層A（フル）と階層C（簡易）で深さを変える。30,000ドメインすべてに
フル計測をかけると GitHub Actions の6時間上限に触れるため、階層分割は必須。

  階層A  8,000 × 約59 = 472,000
  階層C 22,000 ×  約5 = 110,000
                       ─────────
                   計   582,000 クエリ/月
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..config import load_measure_config
from ..contracts import MeasureTier, QueryPurpose

#: 対照クエリに使うラベル。実在しないセレクタを引いて、
#: 何にでも応答するワイルドカードDNSを検出する（偽陽性ガード）。
#: ドメインごとに決まった値にする。原則6（同じ入力なら同じ出力）のため
CONTROL_LABEL_PREFIX = "mailauth-control-"


def control_label(domain: str) -> str:
    digest = hashlib.sha256(f"control|{domain}".encode()).hexdigest()[:12]
    return f"{CONTROL_LABEL_PREFIX}{digest}"


def subdomain_label(domain: str) -> str:
    """存在しないサブドメイン。DMARC の np=（存在しないサブドメイン用）の実効確認。"""
    digest = hashlib.sha256(f"subdomain|{domain}".encode()).hexdigest()[:12]
    return f"mailauth-nx-{digest}"


@dataclass(frozen=True)
class Query:
    """1本のクエリ。purpose が「何を知りたくて引いたか」を表す。"""

    name: str
    rtype: str
    purpose: str
    #: DKIM の場合のセレクタ名。集計とデバッグのため
    selector: str | None = None

    def key(self) -> tuple[str, str]:
        return (self.name.rstrip(".").lower(), self.rtype.upper())


def build_plan(
    domain: str,
    tier: str,
    *,
    selectors: list[str],
    extras: dict[str, bool] | None = None,
    dkim_control: bool = True,
    dkim_wildcard: bool = True,
) -> list[Query]:
    """1ドメインぶんのクエリ計画。

    階層C に DKIM セレクタを投げないのは二つの理由がある。送信していない
    ドメインに50個投げても検出されないこと。権威DNSへの負荷という点で
    作法が悪いこと。パーク分類に必要なのは MX と SPF と DMARC だけ。
    """
    ex = extras or {}
    queries: list[Query] = [
        Query(domain, "MX", QueryPurpose.MX),
        Query(domain, "TXT", QueryPurpose.SPF),
        Query(f"_dmarc.{domain}", "TXT", QueryPurpose.DMARC),
        # np= の実効確認。存在しないサブドメインの _dmarc を引く
        Query(
            f"_dmarc.{subdomain_label(domain)}.{domain}",
            "TXT",
            QueryPurpose.DMARC_SUBDOMAIN,
        ),
    ]

    # 失効鍵の検出。ワイルドカードで p= を空にしておくのが M3AAWG の推奨構成
    if dkim_wildcard:
        queries.append(
            Query(f"*._domainkey.{domain}", "TXT", QueryPurpose.DKIM_WILDCARD, selector="*")
        )

    if tier == MeasureTier.C:
        return queries

    # ここから階層A のみ
    for selector in selectors:
        queries.append(
            Query(
                f"{selector}._domainkey.{domain}",
                "TXT",
                QueryPurpose.DKIM,
                selector=selector,
            )
        )
    if dkim_control:
        label = control_label(domain)
        queries.append(
            Query(f"{label}._domainkey.{domain}", "TXT", QueryPurpose.DKIM_CONTROL, selector=label)
        )

    if ex.get("mta_sts", True):
        queries.append(Query(f"_mta-sts.{domain}", "TXT", QueryPurpose.MTA_STS))
    if ex.get("tls_rpt", True):
        queries.append(Query(f"_smtp._tls.{domain}", "TXT", QueryPurpose.TLS_RPT))
    if ex.get("bimi", True):
        queries.append(Query(f"default._bimi.{domain}", "TXT", QueryPurpose.BIMI))

    return queries

def allowed_purposes(tier: str, measure_cfg: dict | None = None) -> set[str] | None:
    """その階層で投げてよい種類。設定に無ければ None（絞らない）。

    `configs/measure.yaml` の `tiers.<階層>.queries` を読む。**以前はここが
    読まれておらず、設定を書き換えても投げる内容は1本も変わらなかった。**
    設定がコードを変えないなら、それは設定ではなく感想である（原則7）。
    """
    cfg = measure_cfg if measure_cfg is not None else load_measure_config()
    spec = ((cfg.get("tiers") or {}).get(tier) or {}).get("queries")
    if not spec:
        return None
    known = {q.value for q in QueryPurpose}
    unknown = [q for q in spec if q not in known]
    if unknown:
        # **黙って全部落とさない。** 綴り違いで計測が空になる方が重い
        raise ValueError(
            f"tiers.{tier}.queries に未知の種類がある: {unknown}。"
            f"使えるのは {sorted(known)}"
        )
    return set(spec)


def restrict_to_configured(
    queries: list[Query], tier: str, measure_cfg: dict | None = None
) -> list[Query]:
    """設定に挙がっていない種類を落とす。

    DANE は MX が分かってから足すので、P4 側から2度呼ばれる
    （組んだ直後と、DANE を足したあと）。
    """
    allowed = allowed_purposes(tier, measure_cfg)
    if allowed is None:
        return queries
    return [q for q in queries if q.purpose in allowed]



def build_dane_queries(mx_hosts: list[str], limit: int = 9) -> list[Query]:
    """DANE の TLSA。MX ホストが分かってからでないと組めないので別関数。

    日米ではほぼ普及していないが、Global 500 で欧州企業が母集団に入ると
    地域差が最も鮮明に出る指標のひとつになる（DESIGN.md P4「DANE の位置づけ」）。
    DNSSEC 署名の有無を必ず併記すること。TLSA はあるが親ゾーンが未署名で
    実効しない、という誤設定を検出できる。
    """
    return [
        Query(f"_25._tcp.{host}", "TLSA", QueryPurpose.DANE)
        for host in mx_hosts[:limit]
    ]


def estimate_queries(tier_counts: dict[str, int], selector_count: int) -> dict[str, int]:
    """クエリ量の見積り。実行前に6時間上限に触れないかを確認するために使う。"""
    # 階層A: MX/SPF/DMARC/DMARC_sub/wildcard + セレクタ + 対照 + extras3
    per_a = 5 + selector_count + 1 + 3
    per_c = 5
    a = tier_counts.get(MeasureTier.A, 0)
    c = tier_counts.get(MeasureTier.C, 0)
    return {
        "tier_a_domains": a,
        "tier_c_domains": c,
        "per_tier_a": per_a,
        "per_tier_c": per_c,
        "total_estimate": a * per_a + c * per_c,
    }
