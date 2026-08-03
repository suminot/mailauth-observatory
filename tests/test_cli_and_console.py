"""CLI と運用コンソール API。"""

from __future__ import annotations

import datetime as dt

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


def test_stub_writes_a_manifest_before_stopping(monkeypatch):
    """未実装でも manifest は残す。コンソールで「未実装」と見えるようにするため。

    現在は全フェーズが実装済みなので、仕組み自体が生きていることを確認する。
    フェーズを追加したときに「何もせず成功したふりをする」実装が入り込むのを
    防ぐための機構なので、使われていなくても壊さない。
    """
    import mailauth.stubs as stubs_mod
    from mailauth.manifest import read_manifest
    from mailauth.paths import phase_dir

    monkeypatch.setitem(stubs_mod.PLANNED_SPRINT, "p9_future", "Sprint 99")
    with pytest.raises(PhaseNotImplementedError):
        not_implemented("p9_future", "2026-08")
    manifest = read_manifest(phase_dir("2026-08", "p9_future"))
    assert manifest["status"] == "failed"
    assert manifest["breakdown"]["planned_sprint"] == "Sprint 99"
    # 空の出力を作らない。下流が「0件だった」と解釈しないように
    assert manifest["outputs"] == []


def test_every_phase_is_implemented():
    """八工程すべてが実装済みで、スタブ表は空であること。"""
    assert PLANNED_SPRINT == {}


def test_implemented_phases_are_not_stubs():
    from mailauth import PHASES
    from mailauth.cli import IMPLEMENTED

    assert IMPLEMENTED == set(PHASES)
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


# -- 画面5 辞書メンテナンス --------------------------------------------------


@pytest.fixture
def sandbox_configs(tmp_path, monkeypatch):
    """辞書を書き換えるテストがリポジトリの configs/ を汚さないようにする。

    MAILAUTH_ROOT を差し替えると config_path の解決先が移る。
    """
    import shutil

    from mailauth.paths import repo_root

    real = repo_root()
    root = tmp_path / "sandbox"
    (root / "configs").mkdir(parents=True)
    for name in ("fingerprints", "vendors", "dkim_selectors"):
        shutil.copytree(real / "configs" / name, root / "configs" / name)
    shutil.copy(real / "configs" / "measure.yaml", root / "configs" / "measure.yaml")
    monkeypatch.setenv("MAILAUTH_ROOT", str(root))
    return root


def test_unknown_hosts_requires_p6(client):
    resp = client.get("/api/dict/unknown-hosts", params={"run": "2099-01"})
    assert resp.status_code == 404
    assert "p6-infer" in resp.json()["detail"]


def test_unknown_hosts_are_returned_in_frequency_order(client):
    """辞書を育てる主要な経路。頻度順に並んでいること。"""
    from mailauth.contracts import FACT_ARROW_SCHEMA
    from mailauth.io import write_parquet
    from mailauth.p6_infer import run as run_p6
    from mailauth.paths import phase_output

    rows = []
    for i in range(4):
        rows.append(
            {
                "fact_id": f"f:{i}", "domain_id": f"d:{i}", "entity_id": "jp:1",
                "run_id": "2026-08", "measured_month": dt.date(2026, 8, 1),
                "observed": True,
                "mx_hosts": ["mx1.unknown-a.example" if i < 3 else "mx1.unknown-b.example"],
                "mx_present": True,
            }
        )
    write_parquet(rows, phase_output("2026-08", "p5_parse", "facts.parquet"),
                  FACT_ARROW_SCHEMA)
    run_p6(run_id="2026-08")

    body = client.get("/api/dict/unknown-hosts", params={"run": "2026-08"}).json()
    assert [h["registered_domain"] for h in body["hosts"]] == [
        "unknown-a.example", "unknown-b.example",
    ]
    assert body["hosts"][0]["count"] == 3


def test_adding_a_rule_appends_to_the_dictionary(client, sandbox_configs):
    """3クリックで辞書に足せること。コメントは消さないこと（DESIGN.md 7.2）。"""
    target = sandbox_configs / "configs" / "fingerprints" / "security_gw.yaml"
    before = target.read_text(encoding="utf-8")

    resp = client.post(
        "/api/dict/rules",
        json={
            "file": "configs/fingerprints/security_gw.yaml",
            "id": "nri-mx-01",
            "vendor": "NRIセキュアテクノロジーズ",
            "record": "MX",
            "pattern": r"\.nri-secure\.example\.?$",
            "region": "JP",
            "confidence": "high",
        },
    )
    assert resp.status_code == 200, resp.text

    after = target.read_text(encoding="utf-8")
    # 既存のコメントが1行も消えていないこと
    for line in before.splitlines():
        if line.strip().startswith("#"):
            assert line in after
    # 別のトップレベルキーの下に紛れ込んでいないこと
    import yaml

    data = yaml.safe_load(after)
    assert data["rules"][-1]["id"] == "nri-mx-01"
    assert len(data["undetectable_by_dns"]) == 6


def test_adding_a_rule_rejects_a_broken_pattern(client, sandbox_configs):
    resp = client.post(
        "/api/dict/rules",
        json={
            "file": "configs/fingerprints/esp.yaml",
            "id": "broken-01", "vendor": "V", "record": "MX", "pattern": "(",
        },
    )
    assert resp.status_code == 400
    assert "コンパイル" in resp.json()["detail"]


def test_adding_a_rule_rejects_a_duplicate_id(client, sandbox_configs):
    resp = client.post(
        "/api/dict/rules",
        json={
            "file": "configs/fingerprints/esp.yaml",
            "id": "pp-mx-01", "vendor": "V", "record": "MX", "pattern": "x",
        },
    )
    assert resp.status_code == 409


def test_adding_a_rule_refuses_paths_outside_the_dictionary(client, sandbox_configs):
    """パスをそのまま書くと任意ファイルを書き換えられる。"""
    for path in ("../../pyproject.toml", "configs/measure.yaml", "/etc/hosts"):
        resp = client.post(
            "/api/dict/rules",
            json={"file": path, "id": "x-01", "vendor": "V",
                  "record": "MX", "pattern": "x"},
        )
        assert resp.status_code == 400, path


def test_vendor_names_that_break_yaml_are_quoted(client, sandbox_configs):
    """`@` で始まる値は plain scalar として書けない。"""
    resp = client.post(
        "/api/dict/rules",
        json={
            "file": "configs/fingerprints/platforms.yaml",
            "id": "atmark-01", "vendor": "ニフティ", "product": "@nifty メール2",
            "record": "SPF_INCLUDE", "pattern": r"^spf2\.nifty\.example$",
        },
    )
    assert resp.status_code == 200, resp.text
    # 読み直せている（endpoint が検証している）ので YAML として妥当
    body = client.get("/api/dict/fingerprints").json()
    assert any(d["rule_count"] == 12 for d in body["dictionaries"])


# -- 画面6 月次差分 ----------------------------------------------------------


def _make_gold(run_id: str = "2026-08", policy: str = "reject") -> None:
    from mailauth.contracts import (
        ENTITY_ARROW_SCHEMA,
        FACT_ARROW_SCHEMA,
        DkimStatus,
        PolicyLabel,
    )
    from mailauth.io import write_parquet
    from mailauth.p7_aggregate import run as run_p7
    from mailauth.paths import phase_output

    month = dt.date(int(run_id[:4]), int(run_id[5:7]), 1)
    entities = [
        {
            "entity_id": f"jp:{i}", "run_id": run_id, "country": "JP",
            "population_ids": ["jp-all-listed"], "name": f"社{i}",
            "name_normalized": f"{i}", "common12_code": "1",
            "common12_label": "業種1", "status": "active",
        }
        for i in range(6)
    ]
    write_parquet(entities, phase_output(run_id, "p1_population", "entities.parquet"),
                  ENTITY_ARROW_SCHEMA)
    facts = [
        {
            "fact_id": f"f:{i}", "domain_id": f"d:{i}", "entity_id": f"jp:{i}",
            "run_id": run_id, "measured_month": month,
            "observed": True, "record_present": True,
            "spf_present": True, "dmarc_present": True, "dmarc_p": policy,
            "effective_7489": policy, "dkim_status": DkimStatus.DETECTED,
            "policy_label": (
                PolicyLabel.ENFORCED_REJECT if policy == "reject" else PolicyLabel.MONITORING
            ),
        }
        for i in range(6)
    ]
    write_parquet(facts, phase_output(run_id, "p5_parse", "facts.parquet"),
                  FACT_ARROW_SCHEMA)
    run_p7(run_id=run_id)


def test_gold_months_are_empty_before_p7(client):
    assert client.get("/api/gold/months").json()["months"] == []


def test_gold_month_requires_p7(client):
    resp = client.get("/api/gold/2099-01")
    assert resp.status_code == 404
    assert "p7-aggregate" in resp.json()["detail"]


def test_gold_month_shows_the_stats_p7_wrote(client):
    """コンソールが gold を再計算しないこと。数字が食い違うと信用できない。"""
    _make_gold()
    body = client.get("/api/gold/2026-08").json()

    assert body["month"] == "2026-08"
    assert body["previous_month"] == "2026-07"
    stats = body["overall"][0]
    assert stats["observed_domains"] == 6
    assert stats["enforced_reject_domains"] == 6
    # JSON 文字列ではなく構造として返す
    assert stats["maturity_stage_dist"]["2"] == 6
    assert stats["delta_prev_month"]["skipped"] is True
    assert stats["diff_prev_month"] is None


def test_gold_month_reports_the_previous_month_diff(client):
    _make_gold("2026-07", policy="none")
    _make_gold("2026-08", policy="reject")

    stats = client.get("/api/gold/2026-08").json()["overall"][0]
    assert stats["previous_month"] == "2026-07"
    assert stats["diff_prev_month"]["enforced_reject_domains"] == 6
    assert stats["delta_prev_month"]["policy_upgraded"] == 6


def test_gold_month_can_filter_by_population(client):
    _make_gold()
    ok = client.get("/api/gold/2026-08", params={"population": "jp-all-listed"})
    assert ok.status_code == 200
    missing = client.get("/api/gold/2026-08", params={"population": "nope"})
    assert missing.status_code == 404
