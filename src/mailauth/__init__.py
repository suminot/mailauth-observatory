"""Email DNS Monitor ── メール認証の月次観測。

設計仕様は DESIGN.md を参照。
"""

__version__ = "0.1.0"

# 全フェーズの一覧。CLI・コンソール・manifest がこの定義を共有する。
PHASES = (
    "p1_population",
    "p2_candidates",
    "p3_domains",
    "p4_measure",
    "p5_parse",
    "p6_infer",
    "p7_aggregate",
    "p8_publish",
)

PHASE_LABELS = {
    "p1_population": "母集団確定",
    "p2_candidates": "ドメイン候補生成",
    "p3_domains": "メールドメイン確定",
    "p4_measure": "DNS計測",
    "p5_parse": "パース",
    "p6_infer": "推察",
    "p7_aggregate": "集計",
    "p8_publish": "公開",
}

# フェーズごとの主たる出力ファイル名（DESIGN.md 5.1）
PHASE_OUTPUTS = {
    "p1_population": "entities.parquet",
    "p2_candidates": "domain_candidates.parquet",
    "p3_domains": "domains.parquet",
    "p4_measure": "bronze/",
    "p5_parse": "facts.parquet",
    "p6_infer": "inferences.parquet",
    "p7_aggregate": "stats_overall.parquet",
    "p8_publish": "site/",
}
