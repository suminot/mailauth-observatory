"""P8 公開。

検証の主眼は「出してはいけないものが出ないこと」。DESIGN.md P8 の
表現上の規約は法務要件でもあるため、警告ではなく停止させる。
  1. 第1層に個社特定情報が混ざったら止まる
  2. 公開ページに断定的な語彙や順位付けがあったら止まる
  3. 第2層は訂正期間を経ていなければ出ない
"""

from __future__ import annotations

import datetime as dt
import json

import pytest
import yaml

from mailauth.contracts import (
    STATS_BY_SECTOR_ARROW_SCHEMA,
    STATS_OVERALL_ARROW_SCHEMA,
)
from mailauth.io import write_parquet
from mailauth.p8_publish import MissingInputError, PublishBlockedError, tiers, vocabulary
from mailauth.p8_publish import run as run_p8
from mailauth.paths import gold_dir

RUN = "2026-08"
MONTH = dt.date(2026, 8, 1)
POP = "jp-all-listed"


# ===========================================================================
# 表現上の規約
# ===========================================================================


@pytest.mark.parametrize(
    "text",
    [
        "この企業のメール設定は危険です",
        "脆弱な状態が続いている",
        "業種別ランキングを掲載する",
        "対策が最下位の企業",
        "無防備なドメイン",
        "放置企業の一覧",
    ],
)
def test_forbidden_terms_are_detected(text):
    """断定的な語彙は使わない。読み手に「この企業は危険だ」と受け取らせる。"""
    violations = vocabulary.check(text)
    assert violations, f"検出できていない: {text}"
    # どこを直すべきかが分かる指摘であること
    assert all(v.advice for v in violations)
    assert all(v.excerpt for v in violations)


@pytest.mark.parametrize(
    "text",
    [
        "総合 A 評価",
        "D ランクの企業",
        "C+ グレード",
        "F 判定",
    ],
)
def test_grade_notation_is_detected(text):
    """総合順位・A〜F グレードは付けない。"""
    assert vocabulary.check(text), f"検出できていない: {text}"


@pytest.mark.parametrize(
    "text",
    [
        "p=none のため spoofing 抑止効果は限定的",
        "業界標準に照らして REQUIRED を満たさない",
        "既知セレクタでは検出できなかった。未設定の証明ではない",
        "Null MX と SPF -all を宣言している",
        "観測できなかったため判定を保留している",
    ],
)
def test_standards_based_statements_pass(text):
    """標準準拠の事実記述は通ること。過検出すると書けなくなる。"""
    assert vocabulary.check(text) == []


def test_disclaimer_must_be_present():
    assert vocabulary.has_disclaimer(vocabulary.DISCLAIMER)
    # 句読点のゆれは許す
    assert vocabulary.has_disclaimer(
        "本サイトは、標準準拠の計測であり、総合的セキュリティ評価ではない。"
    )
    assert not vocabulary.has_disclaimer("計測結果を掲載しています")


def test_policy_descriptions_avoid_assertions():
    """「危険」ではなく「抑止効果は限定的」と書く。"""
    for policy in ("reject", "quarantine", "none", None, "unknown"):
        text = vocabulary.describe_policy(policy)
        assert vocabulary.check(text) == [], text
    assert "限定的" in vocabulary.describe_policy("none")
    # rua が無いことは「見えない」という事実として書く
    assert "確認できない" in vocabulary.describe_policy("reject", has_rua=False)


def test_color_roles_are_three_levels():
    """合格・要改善・未対応の3段階。赤の面積を最小化するため。"""
    assert list(vocabulary.COLOR_ROLES) == ["pass", "attention", "absent"]


# ===========================================================================
# 二層構成
# ===========================================================================


def test_tier2_is_blocked_without_a_notification():
    d = tiers.evaluate_tier2(
        notified_on=None, today=dt.date(2026, 8, 1), access_control_configured=True
    )
    assert d.releasable is False
    assert "事前通知日" in " / ".join(d.reasons)


def test_tier2_is_blocked_before_thirty_days():
    d = tiers.evaluate_tier2(
        notified_on=dt.date(2026, 7, 20),
        today=dt.date(2026, 8, 1),
        access_control_configured=True,
    )
    assert d.releasable is False
    assert d.days_elapsed == 12
    assert "訂正期間" in " / ".join(d.reasons)


def test_tier2_is_blocked_without_access_control():
    """第2層は認証の内側にしか置けない。"""
    d = tiers.evaluate_tier2(
        notified_on=dt.date(2026, 1, 1),
        today=dt.date(2026, 8, 1),
        access_control_configured=False,
    )
    assert d.releasable is False
    assert "アクセス制御" in " / ".join(d.reasons)


def test_tier2_is_releasable_after_the_correction_period():
    d = tiers.evaluate_tier2(
        notified_on=dt.date(2026, 5, 1),
        today=dt.date(2026, 8, 1),
        access_control_configured=True,
    )
    assert d.releasable is True
    assert d.days_elapsed == 92
    assert d.warnings == []


def test_tier2_warns_between_thirty_and_sixty_days():
    """最低条件は満たすが推奨には届かない期間。"""
    d = tiers.evaluate_tier2(
        notified_on=dt.date(2026, 7, 1),
        today=dt.date(2026, 8, 5),
        access_control_configured=True,
    )
    assert d.releasable is True
    assert d.warnings and "推奨" in d.warnings[0]


def test_future_notification_date_is_rejected():
    d = tiers.evaluate_tier2(
        notified_on=dt.date(2027, 1, 1),
        today=dt.date(2026, 8, 1),
        access_control_configured=True,
    )
    assert d.releasable is False
    assert "未来日" in " / ".join(d.reasons)


def test_correction_period_is_thirty_days_minimum():
    assert tiers.MIN_CORRECTION_DAYS == 30
    assert tiers.RECOMMENDED_CORRECTION_DAYS == 60


@pytest.mark.parametrize(
    "column",
    ["entity_id", "domain", "name", "market_segment", "houjin_bangou", "mx_hosts"],
)
def test_tier1_rejects_individual_level_columns(column):
    """フィルタは機械的に適用する。目視の確認は必ず漏れる。"""
    assert tiers.tier1_violations(["measured_month", column]) == [column]


def test_tier1_allows_aggregate_columns():
    assert tiers.tier1_violations(
        ["measured_month", "population_id", "total_domains", "observed_domains"]
    ) == []


# ===========================================================================
# P8 の通し
# ===========================================================================


def _write_gold(month: str = "2026-08", *, extra: dict | None = None) -> None:
    row = {
        "measured_month": dt.date(int(month[:4]), int(month[5:7]), 1),
        "population_id": POP,
        "total_entities": 10,
        "total_domains": 8,
        "observed_domains": 7,
        "spf_adopted_domains": 6,
        "spf_adopted_entities": 5,
        "dmarc_adopted_domains": 4,
        "enforced_reject_domains": 2,
        "maturity_stage_dist": json.dumps({"0": 3, "1": 0, "2": 4, "3": 0, "4": 0}),
    }
    if extra:
        row.update(extra)
    write_parquet(
        [row], gold_dir(month) / "stats_overall.parquet", STATS_OVERALL_ARROW_SCHEMA
    )
    write_parquet(
        [
            {
                **row,
                "common12_code": "1",
                "common12_label": "業種1",
                "n_entities": 10,
                "suppressed": False,
            }
        ],
        gold_dir(month) / "stats_by_sector.parquet",
        STATS_BY_SECTOR_ARROW_SCHEMA,
    )


@pytest.fixture
def publish_config(tmp_path, monkeypatch):
    """site/ とデータの出力先をテスト用に差し替える。"""
    import shutil

    from mailauth.paths import repo_root

    real = repo_root()
    root = tmp_path / "sandbox"
    (root / "configs").mkdir(parents=True)
    shutil.copytree(real / "site" / "src", root / "site" / "src",
                    ignore=shutil.ignore_patterns("data", ".observablehq"))
    (root / "site" / "src" / "data").mkdir(parents=True)

    cfg = yaml.safe_load((real / "configs" / "publish.yaml").read_text(encoding="utf-8"))
    (root / "configs" / "publish.yaml").write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    # 訂正申告の登録簿。**無い状態は「申告0件」ではない**ので、
    # これが無いと第2層は出せない。実リポジトリと同じ状態にしておく
    shutil.copytree(real / "configs" / "corrections", root / "configs" / "corrections")
    monkeypatch.setenv("MAILAUTH_ROOT", str(root))
    return root / "configs" / "publish.yaml"


def _set(config_path, **changes) -> None:
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    for dotted, value in changes.items():
        section, key = dotted.split(".", 1)
        cfg.setdefault(section, {})[key] = value
    config_path.write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def test_p8_requires_gold():
    with pytest.raises(MissingInputError, match="p7-aggregate"):
        run_p8(run_id=RUN)


def test_p8_tolerates_missing_gold_with_any_month(publish_config):
    """gold が無くてもサイトは組める。0% と「未計測」は別物として扱う。"""
    result = run_p8(run_id=RUN, config=str(publish_config), require_month=False)
    assert result["status"] == "success"
    assert any(w["code"] == "NO_GOLD" for w in result["warnings"])

    data = publish_config.parent.parent / "site" / "src" / "data"
    assert json.loads((data / "stats_overall.json").read_text()) == []
    assert json.loads((data / "meta.json").read_text())["latest_month"] is None


def test_p8_writes_the_site_data(publish_config):
    _write_gold()
    result = run_p8(run_id=RUN, config=str(publish_config))
    assert result["status"] == "success"

    data = publish_config.parent.parent / "site" / "src" / "data"
    overall = json.loads((data / "stats_overall.json").read_text())
    assert len(overall) == 1
    assert overall[0]["observed_domains"] == 7

    meta = json.loads((data / "meta.json").read_text())
    assert meta["latest_month"] == "2026-08"
    assert meta["license"] == "CC0-1.0"
    assert meta["attribution"]
    # 検出できないものがあることをサイト側にも渡す
    assert any("痕跡を残さない" in t for t in meta["detection_limits"])
    assert (data / "stats_overall.csv").is_file()


def test_p8_collects_every_month_for_the_time_series(publish_config):
    _write_gold("2026-07")
    _write_gold("2026-08")
    result = run_p8(run_id=RUN, config=str(publish_config))
    assert result["breakdown"]["months"] == ["2026-07", "2026-08"]
    data = publish_config.parent.parent / "site" / "src" / "data"
    assert len(json.loads((data / "stats_overall.json").read_text())) == 2


def test_p8_blocks_individual_level_columns_in_tier1(publish_config):
    """第1層に個社特定情報が混ざったら止まる。"""
    _write_gold()
    # gold に列を足して混入を再現する
    import pyarrow as pa

    schema = pa.schema(
        [*STATS_OVERALL_ARROW_SCHEMA, ("entity_id", pa.string())]
    )
    write_parquet(
        [
            {
                "measured_month": MONTH,
                "population_id": POP,
                "total_entities": 1,
                "entity_id": "jp:1",
            }
        ],
        gold_dir("2026-08") / "stats_overall.parquet",
        schema,
    )
    with pytest.raises(PublishBlockedError, match="entity_id"):
        run_p8(run_id=RUN, config=str(publish_config))


def test_p8_blocks_forbidden_vocabulary_in_pages(publish_config):
    _write_gold()
    page = publish_config.parent.parent / "site" / "src" / "index.md"
    page.write_text(page.read_text(encoding="utf-8") + "\nこの状態は危険です。\n",
                    encoding="utf-8")
    with pytest.raises(PublishBlockedError, match="危険"):
        run_p8(run_id=RUN, config=str(publish_config))


def test_p8_blocks_a_wrong_disclaimer(publish_config):
    _write_gold()
    _set(publish_config, **{"site.disclaimer": "計測結果を掲載しています"})
    with pytest.raises(PublishBlockedError, match="disclaimer"):
        run_p8(run_id=RUN, config=str(publish_config))


def test_p8_does_not_emit_tier2_by_default(publish_config):
    _write_gold()
    result = run_p8(run_id=RUN, config=str(publish_config))
    assert any(w["code"] == "TIER2_DISABLED" for w in result["warnings"])
    assert result["breakdown"]["tier2_enabled"] is False


def test_p8_blocks_tier2_without_the_correction_period(publish_config):
    _write_gold()
    _set(
        publish_config,
        **{
            "tier2.enabled": True,
            "tier2.notified_on": "2026-08-01",
            "tier2.access_control_configured": True,
        },
    )
    with pytest.raises(PublishBlockedError, match="訂正期間"):
        run_p8(run_id=RUN, config=str(publish_config), today=dt.date(2026, 8, 10))


def test_p8_allows_tier2_after_the_correction_period(publish_config, access_verified):
    _write_gold()
    _set(
        publish_config,
        **{
            "tier2.enabled": True,
            "tier2.notified_on": "2026-01-01",
            "tier2.access_control_configured": True,
        },
    )
    result = run_p8(
        run_id=RUN, config=str(publish_config), today=dt.date(2026, 8, 10)
    )
    assert result["status"] == "success"
    assert result["breakdown"]["tier2_enabled"] is True
    assert result["breakdown"]["tier2_days_elapsed"] == 221


def test_p8_records_attribution(publish_config):
    _write_gold()
    result = run_p8(run_id=RUN, config=str(publish_config))
    assert result["attribution"]
    assert any("EDINET" in a for a in result["attribution"])


def test_p8_is_idempotent(publish_config):
    _write_gold()
    run_p8(run_id=RUN, config=str(publish_config))
    data = publish_config.parent.parent / "site" / "src" / "data"
    first = (data / "stats_overall.json").read_bytes()
    run_p8(run_id=RUN, config=str(publish_config))
    assert (data / "stats_overall.json").read_bytes() == first


def test_p8_dry_run_writes_nothing(publish_config):
    _write_gold()
    result = run_p8(run_id=RUN, config=str(publish_config), dry_run=True)
    assert any(w["code"] == "DRY_RUN" for w in result["warnings"])
    data = publish_config.parent.parent / "site" / "src" / "data"
    assert not (data / "stats_overall.json").exists()


def test_p8_refuses_tier2_when_the_corrections_registry_is_unreadable(
    publish_config, access_verified
):
    """**「読めなかった」を「申告0件」として第2層を出さない。**

    未審査の申告があるかどうかが分からない状態で個社名付き明細を出すのは、
    訂正窓口を名目だけにすることになる。第1層は個社を名指ししないので
    止めない（警告に留める）。
    """
    _write_gold()
    (publish_config.parent / "corrections" / "corrections.yaml").unlink()

    # 第1層のみ ── 警告は出るが止まらない
    result = run_p8(run_id=RUN, config=str(publish_config))
    assert result["status"] == "success"
    assert any(
        w["code"] == "CORRECTIONS_REGISTRY_UNREADABLE" for w in result["warnings"]
    )

    _set(
        publish_config,
        **{
            "tier2.enabled": True,
            "tier2.notified_on": "2026-01-01",
            "tier2.access_control_configured": True,
        },
    )
    with pytest.raises(PublishBlockedError) as exc:
        run_p8(run_id=RUN, config=str(publish_config), today=dt.date(2026, 8, 10))
    assert "登録簿が読めていない" in str(exc.value)


def test_p8_lints_the_corrections_history_page(publish_config):
    """訂正履歴も公開ページなので語彙検査の対象になる。"""
    _write_gold()
    page = publish_config.parent.parent / "site" / "src" / "corrections-log.md"
    assert page.is_file(), "訂正履歴ページが site/src に無い"
    result = run_p8(run_id=RUN, config=str(publish_config))
    assert "corrections-log.md" in result["breakdown"]["pages_checked"]
