"""二層構成のアクセス制御（DESIGN.md P8「二層構成」）。

| 層 | 内容 | 条件 |
|---|---|---|
| 第1層 | 全社統計・業種別集計 | 無条件公開（ライセンス上公開可のデータのみ） |
| 第2層 | 個社名付き明細 | 事前通知後、**最低30日（推奨60日）の訂正期間**を経てから |

**第2層は既定で出さない。** 訂正期間を経ていない個社明細を公開してしまうと
取り返しがつかない（キャッシュもインデックスも残る）ため、
「出す」側を明示的な操作にしてある。日付の判定はコードで行い、
運用者の記憶に頼らない。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

#: 訂正期間の最低日数。DESIGN.md P8 は推奨60日、最低30日と定める
MIN_CORRECTION_DAYS = 30
RECOMMENDED_CORRECTION_DAYS = 60

TIER1 = "tier1"
TIER2 = "tier2"


class Tier2NotReleasableError(RuntimeError):
    """第2層の公開条件を満たしていない。**警告ではなく停止させる。**"""


@dataclass
class ReleaseDecision:
    tier: str
    releasable: bool
    days_elapsed: int | None = None
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def evaluate_tier2(
    *,
    notified_on: dt.date | None,
    today: dt.date,
    access_control_configured: bool,
) -> ReleaseDecision:
    """第2層を公開してよいかを判定する。

    `notified_on` は対象企業への事前通知を行った日。None は「通知していない」。
    `access_control_configured` は Cloudflare Access 等の認証が設定済みか。
    """
    decision = ReleaseDecision(tier=TIER2, releasable=False)

    if notified_on is None:
        decision.reasons.append(
            "対象企業への事前通知日が設定されていない。"
            "通知前に個社明細を公開してはならない"
        )
        return decision

    if notified_on > today:
        decision.reasons.append(
            f"事前通知日 {notified_on} が未来日になっている。設定を確認すること"
        )
        return decision

    elapsed = (today - notified_on).days
    decision.days_elapsed = elapsed

    if elapsed < MIN_CORRECTION_DAYS:
        decision.reasons.append(
            f"訂正期間が {elapsed} 日しか経っていない。"
            f"最低 {MIN_CORRECTION_DAYS} 日（推奨 {RECOMMENDED_CORRECTION_DAYS} 日）必要"
        )
        return decision

    if not access_control_configured:
        decision.reasons.append(
            "アクセス制御（Cloudflare Access 等）が設定されていない。"
            "第2層は認証の内側にしか置けない"
        )
        return decision

    decision.releasable = True
    if elapsed < RECOMMENDED_CORRECTION_DAYS:
        decision.warnings.append(
            f"訂正期間は {elapsed} 日。最低条件は満たしているが"
            f"推奨は {RECOMMENDED_CORRECTION_DAYS} 日"
        )
    return decision


#: 第1層に出してはいけない列。
#: market_segment は内部の集計軸専用（DESIGN.md P1）。
#: entity_id / domain / name は個社を特定するので第2層の領域。
TIER1_FORBIDDEN_COLUMNS = frozenset(
    {
        "market_segment",
        "market_segment_source",
        "entity_id",
        "domain",
        "domain_id",
        "name",
        "name_en",
        "name_normalized",
        "houjin_bangou",
        "edinet_code",
        "securities_code",
        "cik",
        "lei",
        "ticker",
        "official_url",
        "official_domain",
        "raw_spf",
        "raw_dmarc",
        "raw_mx",
        "mx_hosts",
        "spf_includes",
        "dkim_selectors",
        "dkim_cname_targets",
        "verification_txt",
        "dmarc_rua",
        "dmarc_ruf",
        "tls_rpt_rua",
    }
)


def tier1_violations(columns: list[str]) -> list[str]:
    """第1層の出力に個社特定情報が混ざっていないか。

    列名で機械的に弾く。DESIGN.md P8 が「フィルタは機械的に適用する」と
    定めているのは、目視の確認が必ず漏れるからである。
    """
    return sorted(set(columns) & TIER1_FORBIDDEN_COLUMNS)
