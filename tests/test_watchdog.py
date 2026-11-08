"""起動漏れの見張り役（2026-11-08）。ネットワークに出ない。"""

from __future__ import annotations

import datetime as dt

from mailauth.watchdog import Run, decide

UTC = dt.UTC
NOW = dt.datetime(2026, 11, 8, 10, 0, tzinfo=UTC)
GOLD_ALL = {"jp-all-listed", "us-all-listed", "jp-all-listed-group"}


def _run(wf, status, minutes_ago, title=""):
    return Run(workflow=wf, status=status, conclusion=None,
               created_at=NOW - dt.timedelta(minutes=minutes_ago), title=title)


def _decide(runs, now=NOW, gold=GOLD_ALL, group=True):
    return decide(now, runs, month="2026-11", gold_populations=gold, has_group_list=group)


def test_補充が50分より長く起動していなければ頼む():
    acts = _decide([_run("ct-backfill.yml", "completed", 70)])
    assert [a.workflow for a in acts] == ["ct-backfill.yml"]
    assert acts[0].inputs == {"run_id": "2026-11"}


def test_補充が走っているか待っていれば頼まない():
    assert _decide([_run("ct-backfill.yml", "completed", 70),
                    _run("ct-backfill.yml", "queued", 1)]) == []
    assert _decide([_run("ct-backfill.yml", "completed", 20)]) == []


def test_月のgoldに母集団が無ければ月次計測を1本だけ頼む():
    acts = _decide([_run("ct-backfill.yml", "in_progress", 1)], gold={"jp-all-listed"})
    assert [a.inputs.get("population") for a in acts] == ["configs/populations/us-all-listed.yaml"]
    assert acts[0].inputs["publish"] == "yes"


def test_月次計測が走っている間は頼まない():
    runs = [_run("ct-backfill.yml", "in_progress", 1), _run("monthly.yml", "in_progress", 5)]
    assert _decide(runs, gold=set()) == []


def test_来ているはずの時刻より前は頼まない():
    early = dt.datetime(2026, 11, 1, 8, 0, tzinfo=UTC)
    runs = [_run("ct-backfill.yml", "in_progress", 1)]
    assert _decide(runs, now=early, gold=set()) == []


def test_落ちた母集団は12時間あけてから頼み直す():
    runs = [_run("ct-backfill.yml", "in_progress", 1),
            _run("monthly.yml", "completed", 60,
                 "月次計測 2026-11 configs/populations/us-all-listed.yaml")]
    gold = {"jp-all-listed", "jp-all-listed-group"}
    assert _decide(runs, gold=gold) == []
    runs[1].created_at = NOW - dt.timedelta(hours=13)
    assert [a.inputs["population"] for a in _decide(runs, gold=gold)] == [
        "configs/populations/us-all-listed.yaml"]


def test_子会社の一覧が無ければグループ会社は頼まない():
    runs = [_run("ct-backfill.yml", "in_progress", 1)]
    assert _decide(runs, gold={"jp-all-listed", "us-all-listed"}, group=False) == []


def test_見張り役は補充と違う分に1時間に2回起動する():
    import yaml

    from mailauth.paths import repo_root

    wf = yaml.safe_load((repo_root() / ".github/workflows/watchdog.yml").read_text())
    on = wf.get("on") or wf.get(True)
    cron = on["schedule"][0]["cron"]
    backfill = yaml.safe_load((repo_root() / ".github/workflows/ct-backfill.yml").read_text())
    bon = backfill.get("on") or backfill.get(True)
    b_minute = bon["schedule"][0]["cron"].split()[0]
    minutes = cron.split()[0].split(",")
    assert len(minutes) == 2 and b_minute not in minutes


def test_見張り役は終わり際に次の見張り役を頼む():
    """schedule はほとんど来ないので、連鎖でつなぐ（2026-11-08）。"""
    import yaml

    from mailauth.paths import repo_root

    wf = yaml.safe_load((repo_root() / ".github/workflows/watchdog.yml").read_text())
    job = wf["jobs"]["watch"]
    last = job["steps"][-1]
    assert last.get("if") == "always()"
    assert "gh workflow run watchdog.yml" in last["run"]
    sleep = int(last["run"].split("sleep ")[1].split()[0])
    # 待つ時間が job の上限に収まる（収まらないと次を頼む前に落ちる）
    assert sleep + 10 * 60 < job["timeout-minutes"] * 60
    assert wf["concurrency"]["group"] == "watchdog"
    assert wf["concurrency"]["cancel-in-progress"] is False
