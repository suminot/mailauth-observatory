"""changelog の自動生成（Sprint 10 / P8「必須ページ」）。

数字が前月と動いたとき、実態が変わったのか計測が変わったのかを読み手が
区別できないと時系列が意味を持たない。検証の主眼は次の3点。
  - 初月を「全部が変更」として出さないこと
  - 版の変化と数字の変化が同じ月に起きたら警告すること
  - 「検出できたこと」と「意味の説明」を混ぜないこと
"""

from __future__ import annotations

import datetime as dt

from typer.testing import CliRunner

from mailauth.changelog import available_months, build_entry, to_json, to_markdown
from mailauth.cli import app
from mailauth.contracts import (
    ENTITY_ARROW_SCHEMA,
    FACT_ARROW_SCHEMA,
    STATS_BY_SECTOR_ARROW_SCHEMA,
    DkimStatus,
    PolicyLabel,
)
from mailauth.io import read_parquet, write_parquet
from mailauth.manifest import RunManifest
from mailauth.paths import gold_dir, phase_dir, phase_output

runner = CliRunner()


def _month(run: str, *, reject: bool = False, sectors: int = 1) -> None:
    from mailauth.p7_aggregate import run as run_p7

    measured = dt.date(int(run[:4]), int(run[5:7]), 1)
    write_parquet(
        [
            {
                "entity_id": f"jp:{i}",
                "run_id": run,
                "country": "JP",
                "population_ids": ["jp-all-listed"],
                "name": f"社{i}",
                "name_normalized": f"{i}",
                "common12_code": str((i % sectors) + 1),
                "common12_label": f"業種{(i % sectors) + 1}",
                "status": "active",
            }
            for i in range(12)
        ],
        phase_output(run, "p1_population", "entities.parquet"),
        ENTITY_ARROW_SCHEMA,
    )
    write_parquet(
        [
            {
                "fact_id": f"f:{i}",
                "domain_id": f"d:{i}",
                "entity_id": f"jp:{i}",
                "run_id": run,
                "measured_month": measured,
                "observed": True,
                "record_present": True,
                "spf_present": True,
                "dmarc_present": True,
                "dmarc_p": "reject" if reject else "none",
                "effective_7489": "reject" if reject else "none",
                "policy_label": (
                    PolicyLabel.ENFORCED_REJECT if reject else PolicyLabel.MONITORING
                ),
                "dkim_status": DkimStatus.DETECTED,
            }
            for i in range(12)
        ],
        phase_output(run, "p5_parse", "facts.parquet"),
        FACT_ARROW_SCHEMA,
    )
    run_p7(run_id=run)


def _set_version(run: str, phase: str, key: str, value: str) -> None:
    with RunManifest(
        run_id=run, phase=phase, out_dir=phase_dir(run, phase),
        tool_versions={key: value},
    ) as m:
        m.counts.input = 1
        m.counts.success = 1


def test_the_first_month_is_not_all_changes():
    """前月が無いだけであって、版が上がったわけではない。"""
    _month("2026-08")
    entry = build_entry("2026-08")
    assert entry.version_changes == []
    assert entry.config_changes == []
    assert entry.has_measurement_change is False
    assert "初回の月" in " / ".join(entry.notes)


def test_metric_changes_are_detected():
    _month("2026-07", reject=False)
    _month("2026-08", reject=True)

    entry = build_entry("2026-08")
    changed = {c.metric: c for c in entry.metric_changes}
    assert changed["enforced_reject_domains"].before == 0
    assert changed["enforced_reject_domains"].after == 12
    assert changed["enforced_reject_domains"].delta == 12
    # 変わっていない指標は出さない
    assert "total_entities" not in changed


def test_version_changes_are_detected():
    _month("2026-07")
    _month("2026-08")
    _set_version("2026-07", "p5_parse", "parser", "1.0.0")
    _set_version("2026-08", "p5_parse", "parser", "2.0.0")

    entry = build_entry("2026-08")
    parser = next(c for c in entry.version_changes if c.key == "parser")
    assert parser.before == "1.0.0"
    assert parser.after == "2.0.0"
    assert entry.has_measurement_change is True


def test_a_measurement_change_alongside_a_metric_change_is_flagged():
    """この月の増減を実態の変化として読んではいけない。"""
    _month("2026-07", reject=False)
    _month("2026-08", reject=True)
    _set_version("2026-07", "p5_parse", "parser", "1.0.0")
    _set_version("2026-08", "p5_parse", "parser", "2.0.0")

    entry = build_entry("2026-08")
    assert entry.metric_changes
    assert "実態の変化として読んではいけない" in " / ".join(entry.notes)


def test_a_changed_industry_classification_breaks_comparability():
    """写像の版が変わると前月との比較が成立しない。"""
    _month("2026-07", sectors=1)
    _month("2026-08", sectors=1)
    # 業種コードの集合を変える
    frame = read_parquet(gold_dir("2026-08") / "stats_by_sector.parquet")
    frame.loc[0, "common12_code"] = "9"
    write_parquet(
        frame,
        gold_dir("2026-08") / "stats_by_sector.parquet",
        STATS_BY_SECTOR_ARROW_SCHEMA,
    )

    entry = build_entry("2026-08")
    assert entry.industry_map_changed is True
    assert "比較が成立しない" in " / ".join(entry.notes)


def test_markdown_leaves_a_blank_for_the_human_interpretation():
    """版が上がったことは機械が言えるが、意味は人が書く。"""
    _month("2026-07")
    _month("2026-08")
    _set_version("2026-07", "p6_infer", "fingerprints", "a=1")
    _set_version("2026-08", "p6_infer", "fingerprints", "a=2")

    text = to_markdown([build_entry("2026-08")])
    assert "### 計測側の変更" in text
    # 人が埋める欄が空いていること
    assert "> 解釈:" in text
    assert "人が書く" in text


def test_markdown_says_when_nothing_changed():
    _month("2026-08")
    text = to_markdown([build_entry("2026-08")])
    assert "計測側の変更はない" in text


def test_months_are_listed_newest_first():
    _month("2026-07")
    _month("2026-08")
    text = to_markdown([build_entry("2026-07"), build_entry("2026-08")])
    assert text.index("## 2026-08") < text.index("## 2026-07")


def test_json_output_carries_the_flags():
    _month("2026-07", reject=False)
    _month("2026-08", reject=True)
    import json as _json

    payload = _json.loads(to_json([build_entry("2026-08")]))
    assert payload[0]["month"] == "2026-08"
    assert payload[0]["metric_changes"]
    assert payload[0]["has_measurement_change"] is False


def test_available_months_reads_gold():
    _month("2026-07")
    _month("2026-08")
    assert available_months() == ["2026-07", "2026-08"]


# ===========================================================================
# CLI
# ===========================================================================


def test_cli_writes_the_site_page(tmp_path):
    _month("2026-08")
    out = tmp_path / "changelog.md"
    result = runner.invoke(app, ["changelog", "--out", str(out)])
    assert result.exit_code == 0
    assert "変更履歴" in out.read_text(encoding="utf-8")


def test_cli_warns_when_an_interpretation_is_pending(tmp_path):
    _month("2026-07")
    _month("2026-08")
    _set_version("2026-07", "p5_parse", "parser", "1.0.0")
    _set_version("2026-08", "p5_parse", "parser", "2.0.0")

    result = runner.invoke(
        app, ["changelog", "--out", str(tmp_path / "c.md")]
    )
    assert result.exit_code == 0
    assert "解釈" in result.output


def test_cli_says_when_there_is_no_gold(tmp_path):
    result = runner.invoke(app, ["changelog", "--out", str(tmp_path / "c.md")])
    assert result.exit_code == 0
    assert "gold がまだありません" in result.output
