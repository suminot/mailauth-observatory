"""実行記録の集約（Sprint 8「自動化」）。

月次実行を人手を介さず回すには、終わったあとに何が起きたかを1つの
ファイルから読めることが要る。検証の主眼は次の2点。
  - 「失敗」と「未実行」を混同しないこと
  - 工程間で何件落ちたかが出ること
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from mailauth import PHASES
from mailauth.cli import app
from mailauth.manifest import RunManifest
from mailauth.paths import phase_dir
from mailauth.report import NOT_RUN, collect, to_markdown

RUN = "2026-08"
runner = CliRunner()


def _manifest(phase: str, *, input_=10, success=10, failed=0, error=None, warn=None):
    with RunManifest(
        run_id=RUN, phase=phase, out_dir=phase_dir(RUN, phase)
    ) as m:
        m.counts.input = input_
        m.counts.success = success
        m.counts.failed = failed
        if warn:
            m.add_warning(warn, message="テスト")
        if error:
            m.error = error
            from mailauth.manifest import STATUS_FAILED

            m.force_status(STATUS_FAILED)


def test_nothing_run_is_not_a_failure():
    """未実行を失敗として扱うと、部分実行のたびに赤くなる。"""
    phase_dir(RUN, "p1_population").mkdir(parents=True, exist_ok=True)
    report = collect(RUN)
    assert report.status == NOT_RUN
    assert report.failed_phases == []
    assert len(report.phases) == len(PHASES)
    assert all(not p.ran for p in report.phases)


def test_partial_run_is_not_a_failure():
    _manifest("p1_population")
    _manifest("p2_candidates")
    report = collect(RUN)
    assert report.status == "success"
    assert [p.phase for p in report.ran_phases] == ["p1_population", "p2_candidates"]
    assert [p.phase for p in report.phases if not p.ran][0] == "p3_domains"


def test_a_failed_phase_fails_the_run():
    _manifest("p1_population")
    _manifest("p2_candidates", error="gBizINFO が落ちた")
    report = collect(RUN)
    assert report.status == "failed"
    assert [p.phase for p in report.failed_phases] == ["p2_candidates"]


def test_a_partial_phase_makes_the_run_partial():
    _manifest("p1_population", failed=2)
    report = collect(RUN)
    assert report.status == "partial"


def test_flow_shows_where_records_were_lost():
    """どこで何件落ちたかが最重要の情報。"""
    _manifest("p1_population", input_=100, success=90)
    _manifest("p2_candidates", input_=90, success=40)
    _manifest("p3_domains", input_=40, success=30)

    report = collect(RUN)
    assert [f["delta"] for f in report.flow] == [0, 0]
    assert report.flow[0]["out"] == 90
    assert report.flow[0]["in"] == 90


def test_flow_skips_unrun_phases():
    """未実行の工程を挟んで件数を比べると意味の無い差分が出る。"""
    _manifest("p1_population", input_=100, success=90)
    _manifest("p5_parse", input_=90, success=80)
    report = collect(RUN)
    assert len(report.flow) == 1
    assert report.flow[0]["from"] == "p1_population"
    assert report.flow[0]["to"] == "p5_parse"


def test_markdown_lists_all_eight_phases():
    _manifest("p1_population")
    text = to_markdown(collect(RUN))
    for phase in PHASES:
        assert phase in text
    assert "未実行の工程" in text
    assert "失敗ではない" in text


def test_markdown_records_warnings_and_failures():
    _manifest("p1_population", warn="ACCEPTANCE_COUNT_OUT_OF_RANGE")
    _manifest("p2_candidates", error="接続できない")
    text = to_markdown(collect(RUN))
    assert "ACCEPTANCE_COUNT_OUT_OF_RANGE" in text
    assert "接続できない" in text
    assert "## 失敗した工程" in text


# ===========================================================================
# CLI
# ===========================================================================


def test_cli_requires_an_existing_run():
    result = runner.invoke(app, ["run-report", "--run", "2099-01"])
    assert result.exit_code == 2


def test_cli_writes_markdown(tmp_path):
    _manifest("p1_population")
    out = tmp_path / "report.md"
    result = runner.invoke(app, ["run-report", "--run", RUN, "--out", str(out)])
    assert result.exit_code == 0
    assert "月次計測" in out.read_text(encoding="utf-8")


def test_cli_can_emit_json():
    _manifest("p1_population")
    result = runner.invoke(app, ["run-report", "--run", RUN, "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["run_id"] == RUN
    assert payload["status"] == "success"
    assert len(payload["not_run"]) == len(PHASES) - 1


def test_cli_fails_only_on_a_failed_phase():
    """未実行では落とさない。意図的な部分実行を赤くしない。"""
    _manifest("p1_population")
    ok = runner.invoke(app, ["run-report", "--run", RUN, "--fail-on-error"])
    assert ok.exit_code == 0

    _manifest("p2_candidates", error="落ちた")
    bad = runner.invoke(app, ["run-report", "--run", RUN, "--fail-on-error"])
    assert bad.exit_code == 1


@pytest.mark.parametrize("phase", ["p1_population", "p8_publish"])
def test_every_phase_can_be_reported(phase):
    _manifest(phase)
    report = collect(RUN)
    assert any(p.phase == phase and p.ran for p in report.phases)
