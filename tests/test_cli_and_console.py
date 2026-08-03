"""CLI と運用コンソール API。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from mailauth.cli import app
from mailauth.stubs import PLANNED_SPRINT, PhaseNotImplementedError, not_implemented

runner = CliRunner()


# -- CLI --------------------------------------------------------------------


def test_all_eight_phases_are_exposed_as_subcommands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in (
        "p1-population", "p2-candidates", "p3-domains", "p4-measure",
        "p5-parse", "p6-infer", "p7-aggregate", "p8-publish",
    ):
        assert cmd in result.stdout


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "mailauth" in result.stdout


def test_populations_lists_implementation_state():
    result = runner.invoke(app, ["populations"])
    assert result.exit_code == 0
    assert "jp-prime" in result.stdout
    assert "us-fortune500" in result.stdout


def test_p1_runs_from_cli(edinet_sample, jp_config):
    result = runner.invoke(
        app,
        ["p1-population", "--config", jp_config, "--run", "2026-08",
         "--source-file", str(edinet_sample)],
    )
    assert result.exit_code == 0, result.stdout
    assert "status=success" in result.stdout
    assert "entities.parquet" in result.stdout


def test_status_reports_not_run_phases(edinet_sample, jp_config):
    runner.invoke(
        app,
        ["p1-population", "--config", jp_config, "--run", "2026-08",
         "--source-file", str(edinet_sample)],
    )
    result = runner.invoke(app, ["status", "--run", "2026-08"])
    assert result.exit_code == 0
    assert "p1_population" in result.stdout
    assert "not_run" in result.stdout  # P2 以降


@pytest.mark.parametrize("cmd", ["p5-parse", "p6-infer", "p8-publish"])
def test_unimplemented_phases_exit_with_code_2(cmd):
    result = runner.invoke(app, [cmd, "--run", "2026-08"])
    assert result.exit_code == 2
    assert "未実装" in result.output


def test_stub_writes_a_manifest_before_stopping():
    """未実装でも manifest は残す。コンソールで「未実装」と見えるようにするため。"""
    from mailauth.manifest import read_manifest
    from mailauth.paths import phase_dir

    with pytest.raises(PhaseNotImplementedError):
        not_implemented("p5_parse", "2026-08")
    manifest = read_manifest(phase_dir("2026-08", "p5_parse"))
    assert manifest["status"] == "failed"
    assert manifest["breakdown"]["planned_sprint"] == "Sprint 4"


def test_every_unimplemented_phase_declares_its_sprint():
    """実装済みのフェーズはスタブ表から外れていること。"""
    assert set(PLANNED_SPRINT) == {
        "p5_parse", "p6_infer", "p7_aggregate", "p8_publish",
    }


def test_implemented_phases_are_not_stubs():
    from mailauth.cli import IMPLEMENTED

    assert IMPLEMENTED == {"p1_population", "p2_candidates", "p3_domains", "p4_measure"}
    assert not (IMPLEMENTED & set(PLANNED_SPRINT))


def test_invalid_run_id_is_rejected_by_cli(jp_config):
    result = runner.invoke(app, ["p1-population", "--config", jp_config, "--run", "bad"])
    assert result.exit_code != 0


# -- コンソール API ----------------------------------------------------------


@pytest.fixture
def client():
    from console.backend.main import app as console_app

    return TestClient(console_app)


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert len(body["phases"]) == 8


def test_runs_are_listed_after_a_run(client, edinet_sample, jp_config):
    runner.invoke(
        app,
        ["p1-population", "--config", jp_config, "--run", "2026-08",
         "--source-file", str(edinet_sample)],
    )
    assert client.get("/api/runs").json()["runs"] == ["2026-08"]


def test_run_detail_shows_all_eight_phases(client, edinet_sample, jp_config):
    """画面1 は八工程を必ず並べる。未実行のフェーズも not_run として出す。"""
    runner.invoke(
        app,
        ["p1-population", "--config", jp_config, "--run", "2026-08",
         "--source-file", str(edinet_sample)],
    )
    phases = client.get("/api/runs/2026-08").json()["phases"]
    assert len(phases) == 8
    assert phases[0]["status"] == "success"
    assert phases[0]["counts"]["input"] == 20
    assert phases[0]["warnings"]
    assert all(p["status"] == "not_run" for p in phases[1:])


def test_missing_run_returns_404(client):
    assert client.get("/api/runs/2099-01").status_code == 404


def test_populations_endpoint_flags_unimplemented(client):
    populations = client.get("/api/populations").json()["populations"]
    by_id = {p["id"]: p for p in populations}
    assert by_id["jp-prime"]["implemented"] is True
    assert by_id["us-fortune500"]["implemented"] is False
    assert by_id["us-fortune500"]["blocked_by"]


def test_inspect_returns_json_safe_rows(client, edinet_sample, jp_config):
    """Parquet の list 列や日付が JSON にできる形で返ること。"""
    runner.invoke(
        app,
        ["p1-population", "--config", jp_config, "--run", "2026-08",
         "--source-file", str(edinet_sample)],
    )
    body = client.get("/api/inspect/2026-08/p1_population", params={"limit": 5}).json()
    assert body["total"] == 17
    assert len(body["rows"]) == 5
    row = body["rows"][0]
    assert isinstance(row["population_ids"], list)
    assert isinstance(row["first_seen_month"], str)


def test_inspect_filter(client, edinet_sample, jp_config):
    runner.invoke(
        app,
        ["p1-population", "--config", jp_config, "--run", "2026-08",
         "--source-file", str(edinet_sample)],
    )
    body = client.get("/api/inspect/2026-08/p1_population", params={"q": "銀行"}).json()
    assert body["total"] == 1


def test_dkim_dictionary_state_shows_l3_as_planned(client):
    """コンソールは L3 を「開発予定」と表示する（DESIGN.md 7.2）。"""
    body = client.get("/api/dict/dkim").json()
    assert body["l1"]["status"] == "active"
    assert body["l2"]["status"] == "active"
    assert body["l3"]["status"] == "planned"
    assert body["l3"]["enabled"] is False
    assert "GPL-3.0" in body["l3"]["note"]


def test_fingerprint_dictionary_state(client):
    dictionaries = client.get("/api/dict/fingerprints").json()["dictionaries"]
    assert len(dictionaries) == 5
    assert sum(d["jp_rule_count"] for d in dictionaries) >= 10


def test_job_rejects_unknown_phase(client):
    resp = client.post("/api/jobs", json={"phases": ["p9_nope"]})
    assert resp.status_code == 400


def test_job_rejects_empty_phase_list(client):
    assert client.post("/api/jobs", json={"phases": []}).status_code == 400


def test_job_runs_p1_and_streams_output(edinet_sample, jp_config):
    """画面2 からの実行が、CLI を叩いた場合と同じ結果になること。"""
    import time

    from console.backend.main import app as console_app

    with TestClient(console_app) as c:
        created = c.post(
            "/api/jobs",
            json={
                "phases": ["p1_population"],
                "run_id": "2026-08",
                "config": jp_config,
                "source_file": str(edinet_sample),
            },
        ).json()
        for _ in range(150):
            job = c.get(f"/api/jobs/{created['id']}").json()
            if job["status"] != "running":
                break
            time.sleep(0.2)
        assert job["status"] == "success", job["lines"]
        assert any("entities.parquet" in ln for ln in job["lines"])
        assert c.get("/api/runs/2026-08").json()["phases"][0]["status"] == "success"


def test_p2_p3_run_from_cli(edinet_sample, jp_config, monkeypatch):
    """P2/P3 が CLI から動くこと。DNS には出ないので候補ゼロで成功する。"""
    _run_p1(edinet_sample, jp_config)
    # 起点ドメインが無いので DNS も CT も引かれない。ネットワーク非依存
    r2 = runner.invoke(app, ["p2-candidates", "--run", "2026-08"])
    assert r2.exit_code == 0, r2.output
    assert "NO_SEED_DOMAINS" in r2.output

    r3 = runner.invoke(app, ["p3-domains", "--run", "2026-08"])
    assert r3.exit_code == 0, r3.output
    assert "domains.parquet" in r3.output


def test_p2_without_p1_exits_with_code_2():
    result = runner.invoke(app, ["p2-candidates", "--run", "2026-08"])
    assert result.exit_code == 2
    assert "p1-population" in result.output


def test_p3_without_p2_exits_with_code_2(edinet_sample, jp_config):
    _run_p1(edinet_sample, jp_config)
    result = runner.invoke(app, ["p3-domains", "--run", "2026-08"])
    assert result.exit_code == 2
    assert "p2-candidates" in result.output


def test_console_shows_p2_p3_as_run(client, edinet_sample, jp_config):
    """画面1 に P2/P3 の結果が出ること。"""
    _run_p1(edinet_sample, jp_config)
    runner.invoke(app, ["p2-candidates", "--run", "2026-08"])
    phases = client.get("/api/runs/2026-08").json()["phases"]
    by_id = {p["phase"]: p for p in phases}
    assert by_id["p2_candidates"]["status"] in ("success", "partial")
    assert by_id["p3_domains"]["status"] == "not_run"


def test_job_reports_failure_for_unimplemented_phase():
    import time

    from console.backend.main import app as console_app

    with TestClient(console_app) as c:
        created = c.post(
            "/api/jobs", json={"phases": ["p2_candidates"], "run_id": "2026-08"}
        ).json()
        for _ in range(100):
            job = c.get(f"/api/jobs/{created['id']}").json()
            if job["status"] != "running":
                break
            time.sleep(0.2)
        assert job["status"] == "failed"
        assert job["exit_code"] == 2


# -- ビュー切り替え -----------------------------------------------------------


def _run_p1(edinet_sample, jp_config, run_id="2026-08"):
    return runner.invoke(
        app,
        ["p1-population", "--config", jp_config, "--run", run_id,
         "--source-file", str(edinet_sample)],
    )


def test_views_command_lists_shipped_views():
    result = runner.invoke(app, ["views"])
    assert result.exit_code == 0
    assert "jp-all" in result.stdout
    assert "jp-prime" in result.stdout


def test_view_command_groups_by_industry(edinet_sample, jp_config):
    _run_p1(edinet_sample, jp_config)
    result = runner.invoke(
        app, ["view", "--run", "2026-08", "--view", "jp-all", "--by", "common12"]
    )
    assert result.exit_code == 0
    assert "情報通信" in result.stdout
    assert "合計" in result.stdout


def test_view_command_warns_when_segment_data_missing(edinet_sample, jp_config):
    _run_p1(edinet_sample, jp_config)
    result = runner.invoke(app, ["view", "--run", "2026-08", "--view", "jp-prime"])
    assert result.exit_code == 0
    assert "区分を判定できない" in result.output


def test_view_command_json_output(edinet_sample, jp_config):
    import json as _json

    _run_p1(edinet_sample, jp_config)
    result = runner.invoke(
        app, ["view", "--run", "2026-08", "--view", "jp-all", "--json"]
    )
    assert result.exit_code == 0
    body = _json.loads(result.stdout)
    assert body["view_id"] == "jp-all"
    assert body["total"] == 17


def test_view_command_without_a_run_fails_clearly():
    result = runner.invoke(app, ["view", "--run", "2099-01"])
    assert result.exit_code == 1
    assert "entities.parquet" in result.output


def test_view_command_rejects_unknown_view(edinet_sample, jp_config):
    _run_p1(edinet_sample, jp_config)
    result = runner.invoke(app, ["view", "--run", "2026-08", "--view", "nope"])
    assert result.exit_code == 2


def test_views_endpoint(client):
    body = client.get("/api/views").json()
    assert {v["id"] for v in body["views"]} >= {"jp-all", "jp-prime"}
    assert "common12" in body["group_by_options"]
    assert "segment" in body["group_by_options"]


def test_run_view_endpoint(client, edinet_sample, jp_config):
    _run_p1(edinet_sample, jp_config)
    body = client.get("/api/runs/2026-08/view", params={"view": "jp-all", "by": "common12"}).json()
    assert body["total"] == 17
    assert body["groups"]
    assert body["coverage"]["entities_in_view"] == 17


def test_run_view_endpoint_reports_missing_segment_data(client, edinet_sample, jp_config):
    _run_p1(edinet_sample, jp_config)
    body = client.get("/api/runs/2026-08/view", params={"view": "jp-prime"}).json()
    assert body["total"] == 0
    assert body["segment_data_available"] is False
    assert body["warnings"]


def test_run_view_endpoint_404s_without_a_run(client):
    assert client.get("/api/runs/2099-01/view").status_code == 404


def test_run_view_endpoint_rejects_bad_axis(client, edinet_sample, jp_config):
    _run_p1(edinet_sample, jp_config)
    resp = client.get("/api/runs/2026-08/view", params={"view": "jp-all", "by": "bogus"})
    assert resp.status_code == 400
