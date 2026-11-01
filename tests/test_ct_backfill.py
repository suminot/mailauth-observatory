"""CT の積み残しと、月のあいだの補充（ctlog.py、BACKLOG 12）。

crt.sh は 2026-09 から 2026-11 まで月次計測のたびに落ちていた。P2 が取れ
なかった起点ドメインを積み残しとして書き、別の定期実行が crt.sh（qps 0.5 の
まま）と CertSpotter（無料枠の内側）で取り直す。

テストは一切ネットワークに出ない。httpx は MockTransport で差し替える。
"""

from __future__ import annotations

import json

import httpx
import pytest
from test_p2_p3 import seeded_run  # noqa: F401  （fixture を借りる）

from mailauth.ctlog import (
    CERTSPOTTER_PAGE_SIZE,
    MAX_RERUNS_PER_MONTH,
    CertSpotterClient,
    CrtShClient,
    CtResult,
    backfill,
    list_pending,
    mark_rerun_requested,
    pending_path,
    read_pending,
    write_pending,
)

MONTH = "2026-11"


def _issuance(i: int, *names: str) -> dict:
    return {"id": str(1000 + i), "dns_names": list(names), "not_after": "2027-01-01T00:00:00Z"}


def _certspotter(tmp_path, handler, **kw) -> CertSpotterClient:
    return CertSpotterClient(
        cache_dir=tmp_path,
        month=MONTH,
        min_interval=0,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        **kw,
    )


# ---------------------------------------------------------------------------
# CertSpotter
# ---------------------------------------------------------------------------


def test_certspotter_は証明書の名前を_eTLD1_にまとめて月のキャッシュに書く(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["domain"] == "example.co.jp"
        assert request.url.params["include_subdomains"] == "true"
        assert request.url.params["expand"] == "dns_names"
        return httpx.Response(
            200,
            json=[
                _issuance(1, "www.example.co.jp", "example.co.jp"),
                # **別 apex への飛び移り**がこの経路の値打ち
                _issuance(2, "*.example-mail.jp", "mail.example.co.jp"),
            ],
            headers={"x-ratelimit-remaining": "8"},
        )

    cs = _certspotter(tmp_path, handler)
    result = cs.search("example.co.jp")
    assert result.error is None
    assert result.found == ["example-mail.jp", "example.co.jp"]
    assert result.source == "certspotter"
    payload = json.loads((tmp_path / f"month={MONTH}" / "example.co.jp.json").read_text())
    assert payload == {
        "found": ["example-mail.jp", "example.co.jp"],
        "raw_names": 4,
        "month": MONTH,
        "source": "certspotter",
    }
    # 2回目はキャッシュ。**投げない**
    again = cs.search("example.co.jp")
    assert again.from_cache and again.source == "certspotter"
    assert cs.requests == 1


def test_certspotter_は1ページが埋まっていれば続きを取る(tmp_path):
    calls: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        after = request.url.params.get("after")
        calls.append(after)
        if after is None:
            rows = [_issuance(i, f"h{i}.big.co.jp") for i in range(CERTSPOTTER_PAGE_SIZE)]
            rows[-1]["dns_names"].append("big-group.jp")
            return httpx.Response(200, json=rows)
        return httpx.Response(200, json=[_issuance(999, "last.big.co.jp")])

    result = _certspotter(tmp_path, handler).search("big.co.jp")
    assert calls == [None, str(1000 + CERTSPOTTER_PAGE_SIZE - 1)]
    assert result.found == ["big-group.jp", "big.co.jp"]


def test_certspotter_で取りきれなかったものは_全部として書かない(tmp_path):
    """**途中までを「全部」として書かない**（原則5）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=[_issuance(i, f"h{i}.big.co.jp") for i in range(CERTSPOTTER_PAGE_SIZE)]
        )

    result = _certspotter(tmp_path, handler, max_pages=2).search("big.co.jp")
    assert result.error_kind == "truncated"
    assert not (tmp_path / f"month={MONTH}" / "big.co.jp.json").exists()


def test_certspotter_は上限を使い切ったら次を投げない(tmp_path):
    """鍵なしは1時間に10本。**断られる前にやめる。**"""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["domain"])
        return httpx.Response(
            200, json=[_issuance(1, "a.co.jp")], headers={"x-ratelimit-remaining": "0"}
        )

    cs = _certspotter(tmp_path, handler)
    assert cs.search("a.co.jp").error is None
    second = cs.search("b.co.jp")
    assert second.error_kind == "budget"
    assert seen == ["a.co.jp"]


def test_certspotter_は429で止まる(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "3600"})

    cs = _certspotter(tmp_path, handler)
    assert cs.search("a.co.jp").error_kind == "http"
    assert "429" in (cs.stopped or "")
    assert cs.search("b.co.jp").error_kind == "budget"
    assert cs.requests == 1


def test_certspotter_は1回の実行の本数を超えない(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_issuance(1, request.url.params["domain"])])

    cs = _certspotter(tmp_path, handler, max_requests=2)
    results = [cs.search(f"d{i}.co.jp") for i in range(4)]
    assert [r.error_kind for r in results] == [None, None, "budget", "budget"]
    assert cs.requests == 2


def test_certspotter_は鍵があれば付ける(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer k"
        return httpx.Response(200, json=[])

    assert _certspotter(tmp_path, handler, api_key="k").search("a.co.jp").error is None


def test_crtsh_のキャッシュは取得元を返す(tmp_path):
    """古いキャッシュ（取得元の欄が無い）は crt.sh として読む。"""
    month_dir = tmp_path / f"month={MONTH}"
    month_dir.mkdir(parents=True)
    (month_dir / "old.co.jp.json").write_text(
        json.dumps({"found": ["old.co.jp"], "raw_names": 1, "month": MONTH})
    )
    (month_dir / "cs.co.jp.json").write_text(
        json.dumps({"found": ["cs.co.jp"], "raw_names": 1, "month": MONTH, "source": "certspotter"})
    )
    client = CrtShClient(cache_dir=tmp_path, month=MONTH, qps=0)
    assert client.search("old.co.jp").source == "crtsh"
    assert client.search("cs.co.jp").source == "certspotter"


# ---------------------------------------------------------------------------
# 積み残し
# ---------------------------------------------------------------------------


def test_積み残しは書き直しても流し直しの回数を引き継ぐ(tmp_path):
    write_pending(tmp_path, MONTH, "jp-all-listed", ["b.jp", "a.jp", "a.jp"], total=10)
    mark_rerun_requested(tmp_path, MONTH, "jp-all-listed")
    write_pending(tmp_path, MONTH, "jp-all-listed", ["c.jp"], total=10)
    payload = read_pending(pending_path(tmp_path, MONTH, "jp-all-listed"))
    assert payload["pending"] == ["c.jp"]
    assert payload["reruns_requested"] == 1
    assert payload["total"] == 10


def _crtsh_down(tmp_path) -> CrtShClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="<html>Bad Gateway</html>")

    return CrtShClient(
        cache_dir=tmp_path,
        month=MONTH,
        qps=0,
        retries=0,
        down_after=2,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _crtsh_up(tmp_path) -> CrtShClient:
    def handler(request: httpx.Request) -> httpx.Response:
        domain = request.url.params["q"].removeprefix("%.")
        return httpx.Response(200, json=[{"name_value": f"www.{domain}\n{domain}"}])

    return CrtShClient(
        cache_dir=tmp_path,
        month=MONTH,
        qps=0,
        retries=0,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_crtsh_が戻っていれば_crtsh_で埋める(tmp_path):
    write_pending(tmp_path, MONTH, "jp-all-listed", ["a.jp", "b.jp"], total=100)
    report = backfill(tmp_path, MONTH, crtsh=_crtsh_up(tmp_path), certspotter=None)
    assert report.crtsh["got"] == 2
    assert read_pending(pending_path(tmp_path, MONTH, "jp-all-listed"))["pending"] == []
    assert (tmp_path / f"month={MONTH}" / "a.jp.json").is_file()


def test_crtsh_が落ちていれば_certspotter_の枠の内側で埋める(tmp_path):
    write_pending(tmp_path, MONTH, "jp-all-listed", ["a.jp", "b.jp", "c.jp"], total=100)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["domain"])
        return httpx.Response(200, json=[_issuance(1, request.url.params["domain"])])

    report = backfill(
        tmp_path,
        MONTH,
        crtsh=_crtsh_down(tmp_path),
        certspotter=_certspotter(tmp_path, handler, max_requests=2),
    )
    # crt.sh は2本で見切る（落ちている相手を叩き続けない）
    assert report.crtsh["tried"] == 2
    assert report.crtsh["upstream_down"]
    assert seen == ["a.jp", "b.jp"]
    assert report.certspotter["got"] == 2
    left = read_pending(pending_path(tmp_path, MONTH, "jp-all-listed"))["pending"]
    assert left == ["c.jp"]
    # 積み残しが 1/100 で、取れたものもある → 流し直してよい
    assert report.ready_for_rerun == ["jp-all-listed"]


def test_積み残しが多いうちは流し直さない(tmp_path):
    write_pending(tmp_path, MONTH, "jp-all-listed", [f"d{i}.jp" for i in range(10)], total=100)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    report = backfill(
        tmp_path,
        MONTH,
        crtsh=None,
        certspotter=_certspotter(tmp_path, handler, max_requests=3),
    )
    assert report.populations["jp-all-listed"]["pending_after"] == 7
    assert report.ready_for_rerun == []


def test_流し直しは月に上限回数まで(tmp_path):
    write_pending(tmp_path, MONTH, "jp-all-listed", ["a.jp"], total=100)
    for _ in range(MAX_RERUNS_PER_MONTH):
        mark_rerun_requested(tmp_path, MONTH, "jp-all-listed")
    report = backfill(tmp_path, MONTH, crtsh=_crtsh_up(tmp_path), certspotter=None)
    assert report.populations["jp-all-listed"]["pending_after"] == 0
    assert report.ready_for_rerun == []


def test_何も取れなかった回は流し直さない(tmp_path):
    """積み残しが少なくても、**新しく取れたものが無いなら流し直す意味が無い。**"""
    write_pending(tmp_path, MONTH, "jp-all-listed", ["a.jp"], total=100)
    report = backfill(tmp_path, MONTH, crtsh=_crtsh_down(tmp_path), certspotter=None)
    assert report.ready_for_rerun == []


# ---------------------------------------------------------------------------
# P2 が積み残しを書く
# ---------------------------------------------------------------------------


class _FlakyCt:
    """一部の起点で失敗する CT ソース。キャッシュの置き場所を持つ。"""

    def __init__(self, cache_dir, fail: set[str]):
        self.cache_dir = cache_dir
        self.fail = fail

    def search(self, domain: str) -> CtResult:
        if domain in self.fail:
            return CtResult(domain=domain, error="HTTP 502", error_kind="http")
        return CtResult(domain=domain, found=[domain], raw_names=1)

    def search_org(self, name: str) -> CtResult:
        return CtResult(domain=f"org={name}")

    def prefetch(self, domains, *, on_result=None):
        out = {}
        for d in domains:
            if d and d not in out:
                out[d] = self.search(d)
                if on_result:
                    on_result(d, out[d])
        return out


@pytest.mark.usefixtures("seeded_run")
def test_P2_は取れなかった起点ドメインを積み残しに書く(tmp_path):
    from test_p2_p3 import ANSWERS as answers
    from test_p2_p3 import RUN as run_id
    from test_p2_p3 import SEED_DOMAINS as seeds

    from mailauth.p2_candidates import run as run_p2
    from mailauth.resolver import StaticResolver

    fail = {sorted(seeds.values())[0]}
    result = run_p2(
        run_id=run_id,
        resolver=StaticResolver(answers),
        ct_source=_FlakyCt(tmp_path, fail),
    )
    paths = list_pending(tmp_path, run_id)
    assert len(paths) == 1
    payload = read_pending(paths[0])
    assert payload["population"] == "jp-all-listed"
    assert payload["pending"] == sorted(fail)
    assert payload["total"] == len(set(seeds.values()))
    assert payload["config"] == "configs/populations/jp-all-listed.yaml"
    assert result["breakdown"]["ct_pending"]["pending"] == 1


# ---------------------------------------------------------------------------
# ワークフロー（.github/workflows/ct-backfill.yml）
# ---------------------------------------------------------------------------


def _workflow() -> dict:
    import yaml

    from mailauth.paths import repo_root

    return yaml.safe_load(
        (repo_root() / ".github" / "workflows" / "ct-backfill.yml").read_text(encoding="utf-8")
    )


def _steps() -> list[dict]:
    return _workflow()["jobs"]["backfill"]["steps"]


def _index(name: str) -> int:
    for i, step in enumerate(_steps()):
        if step.get("name") == name:
            return i
    raise AssertionError(f"手順「{name}」が無い")


def test_補充は月次計測が走っている間は触らない():
    """向こうも同じ月の CT キャッシュを読み書きしている。"""
    restore = _steps()[_index("CT ログのキャッシュを復元")]
    fill = _steps()[_index("補充")]
    for step in (restore, fill):
        assert "steps.busy.outputs.monthly_running == '0'" in step.get("if", "")


def test_流し直しを頼んだ記録は保存より前に書く():
    """保存の後に書くと、次の回がそれを知らずにまた頼む。"""
    assert _index("流し直しを頼んだと記録する") < _index("CT ログのキャッシュを保存")
    assert _index("CT ログのキャッシュを保存") < _index("月次計測を流し直す")


def test_補充は相手の上限の内側で投げる():
    """CertSpotter の鍵なしは1時間に10本。毎時9本までなら内側。"""
    wf = _workflow()
    on = wf.get("on") or wf.get(True)
    minute, hour = on["schedule"][0]["cron"].split()[:2]
    assert minute.isdigit() and hour == "*", "1時間に1回より多く動かさない"
    assert on["workflow_dispatch"]["inputs"]["certspotter_requests"]["default"] == "9"
    text = str(_steps()[_index("補充")]["run"])
    assert "--certspotter-requests" in text
    # **IP や経路を変えて制限をすり抜けない**
    full = str(wf).lower()
    for word in ("proxy", "tor ", "socks", "vpn"):
        assert word not in full, f"制限をすり抜ける手段（{word}）を使っている"


def test_米国も毎月測る():
    """国内の schedule とは日を分けて、米国の月次計測を頼む。"""
    import yaml

    from mailauth.paths import repo_root

    wf = yaml.safe_load(
        (repo_root() / ".github" / "workflows" / "monthly-us.yml").read_text(encoding="utf-8")
    )
    on = wf.get("on") or wf.get(True)
    minute, hour, day = on["schedule"][0]["cron"].split()[:3]
    assert day == "2", "国内（1日）と同じ日にしない"
    run = wf["jobs"]["dispatch"]["steps"][0]["run"]
    assert "configs/populations/us-all-listed.yaml" in run
    assert "publish=yes" in run
    # 頼む先の選択肢に米国があること（選べない値を渡すと起動に失敗する）
    monthly = yaml.safe_load(
        (repo_root() / ".github" / "workflows" / "monthly.yml").read_text(encoding="utf-8")
    )
    m_on = monthly.get("on") or monthly.get(True)
    options = m_on["workflow_dispatch"]["inputs"]["population"]["options"]
    assert "configs/populations/us-all-listed.yaml" in options


def test_進んだ回だけ続きを自分で頼む():
    """毎時の schedule は足してから3時間起動しなかった（2026-11-01）。

    **取れなかった回は頼まない** ── 落ちている相手を叩き続けない。
    """
    step = _steps()[_index("続きを頼む")]
    cond = step.get("if", "")
    # **crt.sh から取れた回だけ。** CertSpotter だけで取れた回は続けない
    # （2026-11-01、crt.sh が弱っている間に5分おきに32本ずつ投げ続けた）
    assert "steps.fill.outputs.crtsh_ok == 'true'" in cond
    assert "steps.fill.outputs.got" not in cond
    fill = _steps()[_index("補充")]["run"]
    assert ".crtsh.upstream_down == null" in fill
    assert "steps.fill.outputs.ready == ''" in cond, "流し直しを頼んだ回は続けない"
    assert "steps.fill.outputs.pending != '0'" in cond
    assert "gh workflow run ct-backfill.yml" in step["run"]
    assert _index("続きを頼む") > _index("CT ログのキャッシュを保存"), (
        "保存する前に次の回を頼むと、次の回が古いキャッシュから始める"
    )


def test_月次計測は終わりに補充を頼み_補充は終わるのを待つ():
    import yaml

    from mailauth.paths import repo_root

    monthly = yaml.safe_load(
        (repo_root() / ".github" / "workflows" / "monthly.yml").read_text(encoding="utf-8")
    )
    assert monthly["permissions"].get("actions") == "write"
    steps = {s.get("name"): s for s in monthly["jobs"]["measure"]["steps"]}
    ask = steps["CT の補充を頼む"]
    assert "always()" in ask["if"]
    assert "gh workflow run ct-backfill.yml" in ask["run"]
    busy = _steps()[_index("月次計測が走っていないか確かめる")]["run"]
    assert "sleep" in busy, "頼んだ直後は向こうがまだ実行中なので、待たないと必ず飛ばす"


@pytest.mark.parametrize(
    "workflow, job", [("ct-backfill.yml", "backfill"), ("monthly.yml", "measure")]
)
def test_CT_キャッシュは保存の前にその間の分と混ぜる(workflow, job):
    """補充と月次計測が重なると、後から保存した方が先の方の結果を消す。

    鍵は crtsh-<月>-<run_id> で、復元は一番新しいものを取る。ファイルは
    ドメインごとなので和集合で混ぜ、同じファイルはこの実行の方を残す。
    """
    import yaml

    from mailauth.paths import repo_root

    wf = yaml.safe_load(
        (repo_root() / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    )
    steps = wf["jobs"][job]["steps"]
    names = [s.get("name") for s in steps]
    save = names.index("CT ログのキャッシュを保存")
    stash = names.index("CT キャッシュを退避する")
    restore = names.index("その間に保存された CT キャッシュを復元する")
    overlay = names.index("この実行の CT キャッシュを上から重ねる")
    assert stash < restore < overlay < save
    assert "restore-keys" in steps[restore]["with"]
    assert "cp -a /tmp/crtsh-ours/." in steps[overlay]["run"]
    # 3つとも保存と同じ条件で動く（保存しないのに混ぜても意味が無い）
    for i in (stash, restore, overlay):
        assert steps[i].get("if") == steps[save].get("if")


def test_補充は_crtsh_の応答待ちを重ねる(tmp_path):
    """1本ずつ待つと、応答の中央値 11 秒に対して qps 0.5 の1割も使えず、
    1回 45 分で 150 件前後しか進まなかった（2026-11-01）。P2 と同じく
    `prefetch` で応答待ちだけを重ねる（投げる間隔は全スレッドで共有）。
    """
    import threading
    import time as _time

    domains = [f"d{i}.jp" for i in range(8)]
    write_pending(tmp_path, MONTH, "jp-all-listed", domains, total=100)
    threads: set[int] = set()
    lock = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        with lock:
            threads.add(threading.get_ident())
        _time.sleep(0.05)
        domain = request.url.params["q"].removeprefix("%.")
        return httpx.Response(200, json=[{"name_value": domain}])

    crtsh = CrtShClient(
        cache_dir=tmp_path,
        month=MONTH,
        qps=0,
        retries=0,
        concurrency=4,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    report = backfill(tmp_path, MONTH, crtsh=crtsh, certspotter=None)
    assert report.crtsh["tried"] == 8
    assert report.crtsh["got"] == 8
    assert len(threads) >= 2, "1本ずつ待っている"
    assert read_pending(pending_path(tmp_path, MONTH, "jp-all-listed"))["pending"] == []


def test_補充の同時本数は_P2_より絞る():
    """補充を同時32本にしたら、crt.sh の応答の中央値が 11 秒から 129 秒になり、
    32本中20本が HTTP エラーになった（2026-11-01）。補充は月に何十回も回る。
    """
    import inspect

    import yaml

    from mailauth import cli
    from mailauth.paths import repo_root

    cfg = yaml.safe_load(
        (repo_root() / "configs" / "candidates.yaml").read_text(encoding="utf-8")
    )["ct"]
    assert cfg["backfill_concurrency"] <= 4
    assert cfg["backfill_concurrency"] < cfg["concurrency"]
    assert 'ct_cfg.get("backfill_concurrency"' in inspect.getsource(cli.ct_backfill_cmd)
