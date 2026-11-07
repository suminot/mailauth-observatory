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
        # メール用らしい名前のサブドメインも残す（BACKLOG 52）
        "mail_names": ["mail.example.co.jp"],
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
        # 「補充」は組織名だけの回に名前を変える（月次計測がそれを待たないように）
        if name == "補充" and step.get("id") == "fill":
            return i
    raise AssertionError(f"手順「{name}」が無い")


def test_補充は月次計測が_P2_にいる間は触らず_それ以外は組織名だけを引く():
    """P2 は同じ月の CT キャッシュと積み残しを書く。それ以外の工程の間は、
    組織名の積み残しだけを引く（ドメインの積み残しと流し直しには触らない）。"""
    restore = _steps()[_index("CT ログのキャッシュを復元")]
    fill = _steps()[_index("補充")]
    for step in (restore, fill):
        assert "steps.busy.outputs.mode != 'skip'" in step.get("if", "")
    busy = str(_steps()[_index("月次計測が走っていないか確かめる")]["run"])
    assert "mode=skip" in busy and "*P2*" in busy
    assert "--org-only" in str(fill["run"])


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
    steps = {st["name"]: st for st in wf["jobs"]["dispatch"]["steps"]}
    run = steps["米国の月次計測を頼む"]["run"]
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


def test_月次計測は補充が走っている間は_crt_sh_を叩かない():
    """補充は月次計測を見て始めないが、逆向きが無かった（重なると 0.5 qps の倍）。"""
    import yaml

    from mailauth.paths import repo_root

    monthly = yaml.safe_load(
        (repo_root() / ".github" / "workflows" / "monthly.yml").read_text(encoding="utf-8")
    )
    names = [s.get("name") for s in monthly["jobs"]["measure"]["steps"]]
    wait = names.index("CT の補充が終わるのを待つ")
    assert wait == names.index("P2 ドメイン候補生成") - 1
    run = monthly["jobs"]["measure"]["steps"][wait]["run"]
    assert "ct-backfill.yml" in run and "in_progress" in run and "sleep" in run
    # **補充が crt.sh を叩くと決めたあとだけ待つ。** 補充の確かめの間も待つと
    # お互いを待つ（2026-11-03 に 7 分空いた）。補充側の段の名前で見ているので、
    # 名前を変えたらここが落ちる
    backfill = {s.get("name") for s in _steps()}
    for name in ("月次計測が走っていないか確かめる", "補充"):
        assert name in backfill and f'"{name}"' in run, name


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
    # **P2 も同じ。** 同時本数を上げても crt.sh から取れる件数は増えず、
    # 応答時間とエラーが増えるだけだった（BACKLOG 12）
    assert cfg["concurrency"] <= 4
    assert 'ct_cfg.get("backfill_concurrency"' in inspect.getsource(cli.ct_backfill_cmd)


def test_米国の計測を二重に頼まない():
    """schedule は数時間遅れることがある（2026-11-01 は7時間遅れ）。遅れて
    起動したときに、その月の米国の計測が走っていれば頼まない。

    見分けるために、monthly.yml の実行名に月と母集団を入れる。
    """
    import yaml

    from mailauth.paths import repo_root

    wf = yaml.safe_load(
        (repo_root() / ".github" / "workflows" / "monthly-us.yml").read_text(encoding="utf-8")
    )
    steps = wf["jobs"]["dispatch"]["steps"]
    names = [st["name"] for st in steps]
    assert names.index("もう頼んでいないか確かめる") < names.index("米国の月次計測を頼む")
    check = steps[names.index("もう頼んでいないか確かめる")]["run"]
    assert 'contains(\\"us-all-listed\\")' in check
    assert ".status != " in check, "走っている月次計測があれば頼まない"
    assert steps[names.index("米国の月次計測を頼む")]["if"] == "steps.dup.outputs.found == '0'"

    monthly = (repo_root() / ".github" / "workflows" / "monthly.yml").read_text(encoding="utf-8")
    run_name = yaml.safe_load(monthly)["run-name"]
    assert "inputs.population" in run_name and "inputs.run_id" in run_name


def test_鍵が無ければ_CertSpotter_を使わない():
    """SSLMate は鍵なしの利用を「個人利用か評価目的」に限り、本番では登録を
    求めている（2026-11-02 に確認）。毎月の定期処理は本番である。
    """
    import inspect

    from mailauth import cli

    source = inspect.getsource(cli.ct_backfill_cmd)
    assert "if certspotter_requests > 0 and certspotter_key" in source


# ---------------------------------------------------------------------------
# P2 で CT を取りにいく持ち時間（ct.fetch_budget_minutes）
# ---------------------------------------------------------------------------


def test_持ち時間を過ぎたら_crtsh_に取りにいかずキャッシュだけ返す(tmp_path, monkeypatch):
    """同時本数を上げても crt.sh から取れる件数は増えない（BACKLOG 12）。応答の
    続く限り待つと P2 全体の持ち時間を超え、企業が丸ごと抜ける。"""
    import mailauth.ctlog as ctlog

    now = [1000.0]
    monkeypatch.setattr(ctlog.time, "monotonic", lambda: now[0])
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        domain = request.url.params["q"].removeprefix("%.")
        asked.append(domain)
        return httpx.Response(200, json=[{"name_value": domain}])

    client = CrtShClient(
        cache_dir=tmp_path,
        month=MONTH,
        qps=0,
        retries=0,
        fetch_budget_sec=60,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert client.search("a.jp").found == ["a.jp"]  # 最初の1本で時計を始める
    now[0] += 59
    assert client.search("b.jp").found == ["b.jp"]
    now[0] += 2  # 61 秒経った
    late = client.search("c.jp")
    assert late.error_kind == "fetch_budget"
    assert late.attempts == 0
    assert asked == ["a.jp", "b.jp"], "持ち時間を過ぎても取りにいっている"
    # **キャッシュにあるものは引き続き返す**
    assert client.search("a.jp").from_cache
    # **相手が落ちているのではない。** 落ちた月の扱い（upstream_down）にしない
    assert client.upstream_down is None
    assert client.fetch_budget_skipped == 1
    assert client.response_stats()["fetch_budget_skipped"] == 1


def test_持ち時間_0_なら切らない(tmp_path, monkeypatch):
    import mailauth.ctlog as ctlog

    now = [0.0]
    monkeypatch.setattr(ctlog.time, "monotonic", lambda: now[0])
    client = _crtsh_up(tmp_path)
    assert client.fetch_budget_sec == 0
    client.search("a.jp")
    now[0] += 10**6
    assert client.search("b.jp").error is None


def test_持ち時間で切った分は積み残しに入り_落ちた月と同じく_gold_を縮めない():
    from mailauth.p7_aggregate.runner import DEGRADATION_WARNINGS

    assert "CT_FETCH_BUDGET_SPENT" in DEGRADATION_WARNINGS


def test_p2_の_ct_持ち時間は工程の持ち時間の内側():
    import yaml

    from mailauth.paths import repo_root

    cfg = yaml.safe_load(
        (repo_root() / "configs" / "candidates.yaml").read_text(encoding="utf-8")
    )
    budget = cfg["ct"]["fetch_budget_minutes"]
    assert 0 < budget < cfg["limits"]["max_phase_minutes"], (
        "CT の持ち時間が工程の持ち時間を食い尽くすと、CT 以外の経路の時間が残らない"
    )


def test_見込みは_ct_の待ちを持ち時間で頭打ちにする():
    """序盤に crt.sh を待つ時間がそのまま全体に引き延ばされると、CT は
    持ち時間で切れるのに、工程ごと止めてしまう。"""
    from mailauth.p2_candidates.runner import project_with_ct_budget

    # 100 社中 10 社で 1000 秒（うち CT 900 秒）。素朴な見込みは 10000 秒
    naive = 10_000.0
    got = project_with_ct_budget(naive, done=10, total=100, ct_sec=900, ct_budget_sec=1800)
    # CT 以外 100 秒 → 1000 秒、CT は 1800 秒で頭打ち
    assert got == pytest.approx(1000 + 1800)
    # 持ち時間が無ければ素朴な見込みのまま
    assert project_with_ct_budget(naive, done=10, total=100, ct_sec=900, ct_budget_sec=0) == naive
    # CT が速く終わる調子なら、持ち時間より短く見込む
    fast = project_with_ct_budget(1000.0, done=10, total=100, ct_sec=10, ct_budget_sec=1800)
    assert fast == pytest.approx(900 + 100)
    assert project_with_ct_budget(None, done=0, total=100, ct_sec=0, ct_budget_sec=60) is None


def test_p2_は持ち時間で切ったことを警告に書く(seeded_run):  # noqa: F811
    """**黙って欠けない。** 持ち時間で切った月の CT 由来の候補は欠けている。"""
    from test_p2_p3 import ANSWERS, RUN

    from mailauth.ctlog import StaticCtSource
    from mailauth.p2_candidates.runner import run as run_p2
    from mailauth.resolver import StaticResolver

    class BudgetSpent(StaticCtSource):
        fetch_budget_sec = 60.0
        fetch_budget_skipped = 2

        def search(self, domain: str) -> CtResult:
            return CtResult(domain=domain, error="持ち時間切れ", error_kind="fetch_budget")

    out = run_p2(run_id=RUN, resolver=StaticResolver(ANSWERS), ct_source=BudgetSpent())
    warning = next(w for w in out["warnings"] if w["code"] == "CT_FETCH_BUDGET_SPENT")
    assert warning["count"] == 2
    assert "ct-backfill" in warning["message"]
    # 相手が落ちていた月とは書かない
    assert "CT_UPSTREAM_UNAVAILABLE" not in [w["code"] for w in out["warnings"]]


def test_p2_が途中でやめても_積み残しの分母は母集団の起点全部(seeded_run, tmp_path, monkeypatch):  # noqa: F811
    """2026-11-02 の国内の流し直しは 297 社で打ち切り、積み残しを「290 件中
    12 件」と書いた。補充はそれを「ほぼ片付いた」と読む。"""
    import yaml
    from test_p2_p3 import ANSWERS, RUN, SEED_DOMAINS

    import mailauth.p2_candidates.runner as p2r
    from mailauth.ctlog import StaticCtSource
    from mailauth.p2_candidates.runner import run as run_p2
    from mailauth.paths import config_path
    from mailauth.resolver import StaticResolver

    monkeypatch.setattr(p2r, "PREFETCH_CHUNK", 1)
    cfg = yaml.safe_load(config_path("configs/candidates.yaml").read_text(encoding="utf-8"))
    cfg["limits"]["max_phase_minutes"] = 0.0001
    cfg["limits"]["budget_check_after_fraction"] = 0.0
    path = tmp_path / "candidates-tight.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")

    class Cached(StaticCtSource):
        cache_dir = tmp_path / "crtsh"

    out = run_p2(
        run_id=RUN, resolver=StaticResolver(ANSWERS), ct_source=Cached(), config=str(path)
    )
    assert "PHASE_BUDGET_EXCEEDED" in [w["code"] for w in out["warnings"]]
    ct_pending = out["breakdown"]["ct_pending"]
    assert ct_pending["total"] == len(set(SEED_DOMAINS.values()))
    assert ct_pending["not_reached"] > 0
    # 取りにいっていない起点は積み残しに入る
    assert ct_pending["pending"] == ct_pending["not_reached"]


def test_組織名検索の待ちも_CT_の待ちとして見込みから分ける():
    """組織名検索（1本 40 秒前後）を「CT 以外」に数え、見込みが 240 分を超えて
    国内の流し直しが 297 社で打ち切られた（2026-11-02）。"""
    import inspect

    import mailauth.p2_candidates.runner as p2r

    src = inspect.getsource(p2r.run)
    org_call = src.index("found = ct_source.search_org(name)")
    # 呼び出しの前後で時計を読み、CT の待ちに足している
    window = src[org_call - 200 : org_call + 200]
    assert "time.monotonic()" in window and "ct_wait_sec[0] +=" in window
    assert "ct_sec=ct_wait_sec[0]" in src


def test_組織名検索も先回りして並行に引く(seeded_run):  # noqa: F811
    """1社ずつ順に投げると、応答している日の crt.sh（1本 40 秒前後）で国内の
    約 400 社が CT の持ち時間を食い尽くしていた（2026-11-02）。"""
    from test_p2_p3 import ANSWERS, RUN

    from mailauth.ctlog import StaticCtSource
    from mailauth.p2_candidates.runner import run as run_p2
    from mailauth.resolver import StaticResolver

    class Recording(StaticCtSource):
        def __init__(self):
            super().__init__({}, {})
            self.batches: list[list[str]] = []

        def prefetch_org(self, names):
            names = list(names)
            self.batches.append(names)
            return {n: self.search_org(n) for n in names}

    src = Recording()
    out = run_p2(run_id=RUN, resolver=StaticResolver(ANSWERS), ct_source=src)
    searched = out["breakdown"]["ct_org"]["searched"]
    assert searched > 0, "起点の無い企業がいない fixture では何も見ていない"
    # 先回りで引いた分だけで、1社ずつの呼び出しは追加で起きていない
    prefetched = [n for b in src.batches for n in b]
    assert len(src.org_calls) == len(prefetched)
    assert sorted(set(prefetched)) == sorted(set(src.org_calls))


def test_crtsh_の組織名の先回りは間隔を共有したまま並行に投げる(tmp_path):
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.params["O"]
        asked.append(name)
        return httpx.Response(200, json=[{"name_value": "a.example.jp", "issuer_ca_id": 1}])

    client = CrtShClient(
        cache_dir=tmp_path,
        month=MONTH,
        qps=0,
        retries=0,
        concurrency=4,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    got = client.prefetch_org(["Alpha Corp", "Beta Inc", "Alpha Corp", ""])
    assert list(got) == ["Alpha Corp", "Beta Inc"], "入力順・重複なし"
    assert sorted(asked) == ["Alpha Corp", "Beta Inc"]


def test_2回目の流し直しは取れた量が少なければ頼まない(tmp_path):
    """**数件を埋めただけで2時間半の月次計測を頼まない。** その間は補充も止まる。

    2026-11-02 の国内は、1回目の流し直しのあとに残った数件を補充で埋めただけで
    2回目を頼んでいた。
    """
    from mailauth.ctlog import RERUN_MIN_FILLED

    write_pending(tmp_path, MONTH, "jp-all-listed", ["a.jp", "b.jp"], total=1000)
    mark_rerun_requested(tmp_path, MONTH, "jp-all-listed")
    report = backfill(tmp_path, MONTH, crtsh=_crtsh_up(tmp_path), certspotter=None)
    pop = report.populations["jp-all-listed"]
    assert pop["filled"] == 2 < RERUN_MIN_FILLED
    assert report.ready_for_rerun == []
    assert "2 回目" in pop["rerun_held"]


def test_2回目の流し直しも十分に取れれば頼む(tmp_path):
    from mailauth.ctlog import RERUN_MIN_FILLED

    domains = [f"d{i}.jp" for i in range(RERUN_MIN_FILLED)]
    write_pending(tmp_path, MONTH, "jp-all-listed", domains, total=1000)
    mark_rerun_requested(tmp_path, MONTH, "jp-all-listed")
    report = backfill(tmp_path, MONTH, crtsh=_crtsh_up(tmp_path), certspotter=None)
    assert report.ready_for_rerun == ["jp-all-listed"]


def test_取れなかった回も待ってから続きを頼む():
    """毎時の schedule は 30 時間で4回しか起動しなかった（2026-11-02〜03）。

    取れなかった回で鎖が切れると、補充が半日止まる。**ただし待ってから。**
    落ちている相手を数分おきに叩かない。
    """
    step = _steps()[_index("取れなかった回は待ってから続きを頼む")]
    cond = step["if"]
    assert "steps.fill.outputs.crtsh_ok != 'true'" in cond
    assert "steps.fill.outputs.ready == ''" in cond
    assert "steps.fill.outputs.pending != '0'" in cond
    assert "monthly_running == '0'" in cond, "月次計測の終わりが頼むので、こちらは頼まない"
    run = step["run"]
    assert run.index("sleep") < run.index("gh workflow run ct-backfill.yml")
    wait = int(run.split("sleep", 1)[1].split()[0])
    assert wait >= 15 * 60
    import yaml

    from mailauth.paths import repo_root

    wf = yaml.safe_load(
        (repo_root() / ".github" / "workflows" / "ct-backfill.yml").read_text(encoding="utf-8")
    )
    # 補充 45 分 ＋ 待ちが時間切れにならない
    assert wf["jobs"]["backfill"]["timeout-minutes"] >= 45 + wait // 60 + 5


def test_月次計測はその月の積み残し全部を見て補充を頼む():
    """国内の実行が終わっても、米国の積み残しの補充は再開しなかった。"""
    import yaml

    from mailauth.paths import repo_root

    monthly = yaml.safe_load(
        (repo_root() / ".github" / "workflows" / "monthly.yml").read_text(encoding="utf-8")
    )
    steps = {s.get("name"): s for s in monthly["jobs"]["measure"]["steps"]}
    run = steps["CT の補充を頼む"]["run"]
    assert "_pending/*.json" in run
    assert "steps.run.outputs.pop }}.json" not in run
    # 組織名の積み残しも数える（2026-11-07 の国内は組織名だけが 3,402 社残った）
    assert "_org_pending/*.json" in run


def test_組織名の積み残しを_crtsh_で引いてキャッシュに入れる(tmp_path):
    """起点のある会社の組織名検索は、P2 では取りにいかず補充が埋める（2026-11-07）。"""
    from mailauth.ctlog import org_pending_path, write_org_pending

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.params["O"]
        return httpx.Response(200, json=[
            {"name_value": name, "common_name": "www.brand-x.co.jp"},
            # あいまい一致で返る別会社は採らない
            {"name_value": name + " Credit", "common_name": "www.other.example"},
        ])

    crtsh = CrtShClient(cache_dir=tmp_path, month=MONTH, qps=0, retries=0,
                        client=httpx.Client(transport=httpx.MockTransport(handler)))
    write_org_pending(tmp_path, MONTH, "jp-all-listed", ["Hitachi, Ltd.", "Kao Corporation"])
    report = backfill(tmp_path, MONTH, crtsh=crtsh, certspotter=None)
    assert report.crtsh_org == {"tried": 2, "got": 2, "pending_after": 0}
    assert crtsh.cached_org("Hitachi, Ltd.").found == ["brand-x.co.jp"]
    assert read_pending(org_pending_path(tmp_path, MONTH, "jp-all-listed"))["pending"] == []
    assert "crtsh_org" in report.to_dict()


def test_組織名のキャッシュが無ければ取りにいかない(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("取りにいった")

    crtsh = CrtShClient(cache_dir=tmp_path, month=MONTH, qps=0, retries=0,
                        client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert crtsh.cached_org("Nobody Inc.") is None


def test_組織名だけの回はドメインの積み残しと流し直しに触らない(tmp_path):
    from mailauth.ctlog import write_org_pending

    def handler(request: httpx.Request) -> httpx.Response:
        assert "O" in request.url.params, "ドメインを引いた"
        return httpx.Response(200, json=[{"name_value": request.url.params["O"],
                                          "common_name": "www.x-brand.co.jp"}])

    crtsh = CrtShClient(cache_dir=tmp_path, month=MONTH, qps=0, retries=0,
                        client=httpx.Client(transport=httpx.MockTransport(handler)))
    write_pending(tmp_path, MONTH, "jp-all-listed", ["a.jp"], total=100)
    write_org_pending(tmp_path, MONTH, "jp-all-listed", ["Alpha Corp"])
    report = backfill(tmp_path, MONTH, crtsh=crtsh, certspotter=None, org_only=True)
    assert report.crtsh_org["got"] == 1
    assert report.populations == {} and report.ready_for_rerun == []
    assert read_pending(pending_path(tmp_path, MONTH, "jp-all-listed"))["pending"] == ["a.jp"]



def test_組織名だけの補充は_15_分で切り上げる():
    """次の月次計測の P2 はこの工程が終わるのを待つ（crt.sh を倍の頻度で叩かない）。"""
    fill = _steps()[_index("補充")]
    assert "MINUTES=15" in fill["run"] and '--max-minutes "$MINUTES"' in fill["run"]


def test_CT_の応答からメール用らしい名前のサブドメインを残す():
    """BACKLOG 52：TwoFive との差のうちサブドメイン。CT に載った `news.` などを候補にする。"""
    from mailauth.ctlog import _extract, mail_name

    rows = [
        {"name_value": "www.example.co.jp\nnews.example.co.jp\nmail2.example.co.jp"},
        {"name_value": "imap.example.co.jp\n*.example.co.jp\nm.shop.example.co.jp"},
        {"name_value": "news.other.co.jp\na.b.mail.example.co.jp\nnewsroom.example.co.jp"},
    ]
    r = _extract("example.co.jp", rows)
    assert r.mail_names == ["m.shop.example.co.jp", "mail2.example.co.jp", "news.example.co.jp"]
    assert mail_name("example.co.jp", "example.co.jp") is None
    assert mail_name("e.example.com", "example.com") == "e.example.com"


def test_crtshを使わない母集団は補充を待たない():
    """グループ会社（per_population で CT を切っている）は P2 の前で 24 分待っていた。"""
    import subprocess
    import sys

    import yaml

    from mailauth.paths import repo_root

    wf = yaml.safe_load(
        (repo_root() / ".github" / "workflows" / "monthly.yml").read_text(encoding="utf-8")
    )
    steps = {s.get("name"): s for s in wf["jobs"]["measure"]["steps"]}
    head = steps["CT の補充が終わるのを待つ"]["run"].split("busy() {")[0]
    for pop, want in (("jp-all-listed-group", "0"), ("jp-all-listed", "1")):
        script = (head.replace("${{ steps.run.outputs.pop }}", pop)
                  .replace('>> "$GITHUB_STEP_SUMMARY"', "")
                  .replace("python -", f"{sys.executable} -"))
        out = subprocess.run(["bash", "-c", script + "\necho \"uses_ct=$uses_ct\""],
                             capture_output=True, text=True, cwd=repo_root())
        assert f"uses_ct={want}" in out.stdout, (pop, out.stdout, out.stderr)
