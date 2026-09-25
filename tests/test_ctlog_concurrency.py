"""crt.sh への問い合わせを並行にする。

2026-09 の国内計測で P2 が5時間17分走っても終わらず、ジョブの上限で
打ち切られた。`qps: 0.5` にしているので2秒に1本しか投げない計算だが、
**実測の1件あたりは9.5秒以上あった。** 直列に呼んでいたため、応答を
待っている7秒以上のあいだ何もしていない。

ここで確かめるのは2つで、どちらか片方では意味が無い。

  1. **相手への本数が増えていないこと**（投げる間隔は全スレッド共有）
  2. **応答待ちが実際に重なっていること**（重ならないなら直列と同じ）

1 を落とすと、速くする代わりに crt.sh に迷惑をかけたことになる。
qps を 0.5 に絞っているのは礼儀であって、性能の都合ではない。
"""

from __future__ import annotations

import threading
import time

import httpx
import pytest

from mailauth.ctlog import CrtShClient


class _SlowCrtSh:
    """応答に時間がかかる crt.sh を模す。

    いつ投げられたか、同時に何本が飛んでいたかを記録する。
    """

    def __init__(
        self,
        delay: float = 0.05,
        names: list[str] | None = None,
        delay_for=None,
    ) -> None:
        self.delay = delay
        #: ドメインごとに応答の遅さを変えたいとき。**共有状態を書き換えない**
        #: （worker スレッドから同時に呼ばれる）
        self.delay_for = delay_for
        self.names = names or ["mail.example.jp"]
        self.lock = threading.Lock()
        #: 各リクエストを投げた時刻（monotonic）
        self.issued: list[float] = []
        self.in_flight = 0
        #: 同時に飛んでいた最大本数
        self.max_in_flight = 0
        self.queries: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        q = request.url.params.get("q", "")
        with self.lock:
            self.issued.append(time.monotonic())
            self.queries.append(q)
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            time.sleep(self.delay_for(q) if self.delay_for else self.delay)
        finally:
            with self.lock:
                self.in_flight -= 1
        return httpx.Response(200, json=[{"name_value": "\n".join(self.names)}])


def _client(tmp_path, server: _SlowCrtSh, *, qps: float, concurrency: int) -> CrtShClient:
    return CrtShClient(
        cache_dir=tmp_path / "crtsh",
        month="2026-09",
        qps=qps,
        concurrency=concurrency,
        client=httpx.Client(transport=httpx.MockTransport(server.handle)),
    )


DOMAINS = [f"e{i}.example.jp" for i in range(12)]


# --------------------------------------------------------------------------
# 相手への本数は増えていない
# --------------------------------------------------------------------------


def test_投げる間隔は全スレッドで共有される(tmp_path):
    """**qps を緩めずに速くする**のが目的なので、ここが本題。

    スレッドごとに間隔を持つと、実効 qps が本数倍になる。それは
    「並行にした」ではなく「絞りを外した」である。
    """
    qps = 50.0  # 試験を待たせないための値。間隔は 0.02 秒
    interval = 1.0 / qps
    server = _SlowCrtSh(delay=0.01)

    started = time.monotonic()
    _client(tmp_path, server, qps=qps, concurrency=4).prefetch(DOMAINS)
    elapsed = time.monotonic() - started

    assert len(server.issued) == len(DOMAINS)

    # **全体で見る。** 1本ごとの時刻はスレッドの切り替わりで前後しうるが、
    # 間隔を全スレッドで共有しているなら、N 本を投げ終えるまでに
    # 少なくとも (N-1) 回ぶんの間隔が経っていなければならない。
    # スレッドごとに間隔を持っていると、本数ぶん短くなる（4本なら約 1/4）
    floor = (len(DOMAINS) - 1) * interval
    assert elapsed >= floor * 0.9, (
        f"{len(DOMAINS)} 本を {elapsed:.3f} 秒で投げ終えている"
        f"（間隔 {interval} 秒を守るなら最低 {floor:.3f} 秒）。"
        "スレッドごとに間隔を持っていないか"
    )


def test_並行にしても総本数は変わらない(tmp_path):
    server = _SlowCrtSh()
    _client(tmp_path, server, qps=0, concurrency=4).prefetch(DOMAINS)
    assert len(server.issued) == len(DOMAINS)


def test_同じドメインは2度取らない(tmp_path):
    """同じ起点ドメインを持つ企業が複数あることがある（持株会社など）。"""
    server = _SlowCrtSh()
    result = _client(tmp_path, server, qps=0, concurrency=4).prefetch(
        ["a.example.jp", "b.example.jp", "a.example.jp"]
    )
    assert len(server.issued) == 2
    assert set(result) == {"a.example.jp", "b.example.jp"}


def test_キャッシュにあるものは投げない(tmp_path):
    server = _SlowCrtSh()
    c = _client(tmp_path, server, qps=0, concurrency=4)
    c.prefetch(DOMAINS[:3])
    assert len(server.issued) == 3

    again = _client(tmp_path, server, qps=0, concurrency=4).prefetch(DOMAINS[:3])
    assert len(server.issued) == 3, "キャッシュがあるのに取り直している"
    assert all(r.from_cache for r in again.values())


# --------------------------------------------------------------------------
# 応答待ちが実際に重なっている
# --------------------------------------------------------------------------


def test_応答待ちが重なる(tmp_path):
    """重ならないなら直列と同じで、作った意味が無い。"""
    server = _SlowCrtSh(delay=0.05)
    _client(tmp_path, server, qps=0, concurrency=4).prefetch(DOMAINS)
    assert server.max_in_flight > 1, "同時に1本しか飛んでいない。直列のまま"


def test_同時本数の上限を超えない(tmp_path):
    server = _SlowCrtSh(delay=0.05)
    _client(tmp_path, server, qps=0, concurrency=3).prefetch(DOMAINS)
    assert server.max_in_flight <= 3


def test_直列より速い(tmp_path):
    """実時間が縮んでいること。**これが目的そのもの。**"""
    domains = [f"s{i}.example.jp" for i in range(8)]

    serial_server = _SlowCrtSh(delay=0.05)
    t0 = time.monotonic()
    _client(tmp_path / "a", serial_server, qps=0, concurrency=1).prefetch(domains)
    serial = time.monotonic() - t0

    parallel_server = _SlowCrtSh(delay=0.05)
    t0 = time.monotonic()
    _client(tmp_path / "b", parallel_server, qps=0, concurrency=4).prefetch(domains)
    parallel = time.monotonic() - t0

    assert parallel < serial * 0.7, f"直列 {serial:.3f}秒 に対し並行 {parallel:.3f}秒"


# --------------------------------------------------------------------------
# 結果が実行ごとに変わらない（原則6 冪等）
# --------------------------------------------------------------------------


def test_結果は入力順で返る(tmp_path):
    """**完了順で返すと、同じ入力から違うバイト列が出る。**

    後ろのドメインほど速く返るようにしても、順番が入れ替わらないこと。
    """

    def uneven(q: str) -> float:
        # q は "%.e7.example.jp" の形。e0 が最も遅く、e11 が最も速い
        idx = int(q.split(".")[1].lstrip("e"))
        return 0.06 - idx * 0.004

    server = _SlowCrtSh(delay_for=uneven)
    result = _client(tmp_path, server, qps=0, concurrency=4).prefetch(DOMAINS)
    assert list(result) == DOMAINS


def test_まとめて取っても1件ずつ取っても同じ結果になる(tmp_path):
    server = _SlowCrtSh(delay=0, names=["mail.example.jp", "www.example.net"])
    direct = _client(tmp_path / "a", server, qps=0, concurrency=1).search("example.jp")
    bulk = _client(tmp_path / "b", server, qps=0, concurrency=4).prefetch(["example.jp"])
    assert bulk["example.jp"].found == direct.found
    assert bulk["example.jp"].raw_names == direct.raw_names


def test_取れなかったものは空ではなく誤りとして返る(tmp_path):
    """原則5。**「取れなかった」を「候補が無かった」にしない。**"""

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("切断")

    c = CrtShClient(
        cache_dir=tmp_path / "crtsh",
        month="2026-09",
        qps=0,
        retries=0,
        concurrency=4,
        client=httpx.Client(transport=httpx.MockTransport(boom)),
    )
    result = c.prefetch(["example.jp"])
    assert result["example.jp"].error
    assert result["example.jp"].found == []


def test_取れなかったものをキャッシュに残さない(tmp_path):
    """残すと、次の実行も「候補なし」のまま固定される。"""

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("切断")

    cache = tmp_path / "crtsh"
    CrtShClient(
        cache_dir=cache,
        month="2026-09",
        qps=0,
        retries=0,
        concurrency=4,
        client=httpx.Client(transport=httpx.MockTransport(boom)),
    ).prefetch(["example.jp"])
    assert not list(cache.rglob("*.json")), "失敗をキャッシュに書いている"


# --------------------------------------------------------------------------
# 組み込まれていること
# --------------------------------------------------------------------------


def test_P2が先回り取得を使っている():
    """**外すと5時間コースに戻る。**

    直列に戻したことは、結果を見ても分からない（同じ数字が出る）。
    遅いだけなので、検査で止める。
    """
    import inspect

    from mailauth.p2_candidates import runner

    source = inspect.getsource(runner)
    assert ".prefetch(" in source, "P2 が CT ログを先回りして取っていない"


def test_塊の大きさが同時本数より十分に大きい():
    """塊の終わりで待ち合わせるので、同時本数と同程度だと並行が利かない。"""
    from mailauth.ctlog import DEFAULT_CONCURRENCY
    from mailauth.p2_candidates.runner import PREFETCH_CHUNK

    assert PREFETCH_CHUNK >= DEFAULT_CONCURRENCY * 4


@pytest.mark.parametrize("concurrency", [0, -1])
def test_同時本数は1を下回らない(tmp_path, concurrency):
    server = _SlowCrtSh()
    c = _client(tmp_path, server, qps=0, concurrency=concurrency)
    assert c.concurrency == 1
    c.prefetch(["example.jp"])
    assert len(server.issued) == 1


# ===========================================================================
# 何に待たされたかを残す
#
# **2026-09 の P2 は4時間21分かかったが、残っていたのは失敗の件数だけ**
# だった。時間切れと HTTP エラーの区別も、どれだけ待たされたかも無い。
# 判断材料が無いまま調整すると、次の実行も5時間かけて同じことを学ぶ。


def _mock_client(handler, **kw):
    import httpx

    from mailauth.ctlog import CrtShClient

    return CrtShClient(
        qps=0, retries=0, client=httpx.Client(transport=httpx.MockTransport(handler)), **kw
    )


def test_時間切れと_http_エラーを分けて数える():
    import httpx

    def handler(request):
        if "slow" in str(request.url):
            raise httpx.ReadTimeout("too slow", request=request)
        return httpx.Response(502, text="bad gateway")

    client = _mock_client(handler)
    slow = client.search("slow.example.jp")
    bad = client.search("bad.example.jp")

    assert slow.error_kind == "timeout"
    assert bad.error_kind == "http"
    stats = client.response_stats()
    assert stats["errors_by_kind"] == {"timeout": 1, "http": 1}, (
        "失敗を一語にまとめている。**時間切れなら待ち方、HTTP なら頼み方**"
    )


def test_失敗にも待たされた時間が残る():
    import httpx

    def handler(request):
        raise httpx.ReadTimeout("too slow", request=request)

    client = _mock_client(handler)
    result = client.search("slow.example.jp")
    assert result.duration_sec is not None, (
        "**待たされた時間が残っていない。** 実時間の大半がここなのに分からない"
    )
    assert client.response_stats()["requests"] == 1


def test_応答時間の分布が出る():
    import httpx

    def handler(request):
        return httpx.Response(200, json=[{"name_value": "a.example.jp"}])

    client = _mock_client(handler)
    for i in range(10):
        client.search(f"d{i}.example.jp")

    stats = client.response_stats()
    assert stats["requests"] == 10
    for key in ("p50_sec", "p90_sec", "p99_sec", "max_sec", "mean_sec"):
        assert key in stats, f"{key} が無い"
    assert stats["p50_sec"] <= stats["p90_sec"] <= stats["max_sec"]
    # **どこまで待つ設定だったかも一緒に残す。** 分布だけ見ても、
    # 時間切れが「設定が短い」のか「相手が遅い」のか判断できない
    assert stats["timeout_sec"] == client.timeout


def test_1本も投げていないことを_0秒と区別する():
    """**測っていないことと、0 秒だったことは別である**（原則5）。"""
    import httpx

    client = _mock_client(lambda r: httpx.Response(200, json=[]))
    stats = client.response_stats()
    assert stats["requests"] == 0
    assert "p50_sec" not in stats, "投げていないのに分布を出している"


def test_キャッシュに当たった分を応答時間に混ぜない(tmp_path):
    """**キャッシュは crt.sh の速さではない。** 混ぜると分布が嘘になる。"""
    import httpx

    def handler(request):
        return httpx.Response(200, json=[{"name_value": "a.example.jp"}])

    client = _mock_client(handler, cache_dir=tmp_path, month="2026-09")
    client.search("d.example.jp")
    assert client.response_stats()["requests"] == 1

    again = client.search("d.example.jp")
    assert again.from_cache, "この検査の前提（2度目がキャッシュに当たる）が崩れている"
    assert client.response_stats()["requests"] == 1, (
        "キャッシュに当たった分を crt.sh の応答として数えている"
    )


def test_投げ直した件数を数えている():
    import httpx

    state = {"n": 0}

    def handler(request):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(503, text="try later")
        return httpx.Response(200, json=[{"name_value": "a.example.jp"}])

    import mailauth.ctlog as mod
    from mailauth.ctlog import CrtShClient

    # 投げ直しの待ちで検査を止めない
    original = mod.time.sleep
    mod.time.sleep = lambda _s: None
    try:
        client = CrtShClient(
            qps=0, retries=2, client=httpx.Client(transport=httpx.MockTransport(handler))
        )
        result = client.search("d.example.jp")
    finally:
        mod.time.sleep = original

    assert result.attempts == 2
    assert client.response_stats()["retried"] == 1


def test_p2_の実行記録に応答時間が入っている():
    """**manifest に出ていなければ、実行のあとで読めない。**"""
    import inspect

    from mailauth.p2_candidates import runner as mod

    source = inspect.getsource(mod.run)
    assert "ct_response" in source, "P2 の manifest に応答時間を載せていない"


# --------------------------------------------------------------------------
# 相手が落ちているときは止める
#
# 2026-09-24、crt.sh は**トップページごと 502 Bad Gateway** を返していた。
# 3,191ドメインを順に投げても1本も取れず、投げ直しに枠を使い切るだけで、
# しかも落ちている相手を叩き続けることになる。
# --------------------------------------------------------------------------


class _DeadCrtSh:
    """全部 502 を返す crt.sh。実際に叩かれた本数を数える。"""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        with self.lock:
            self.requests += 1
        return httpx.Response(502, text="502 Bad Gateway")


def _dead_client(tmp_path, server, *, down_after: int) -> CrtShClient:
    return CrtShClient(
        cache_dir=tmp_path / "crtsh",
        month="2026-09",
        qps=0.0,  # 試験を待たせない
        retries=0,  # 投げ直しの回数は別の話なので混ぜない
        concurrency=1,
        down_after=down_after,
        client=httpx.Client(transport=httpx.MockTransport(server.handle)),
    )


def test_全部失敗し続けたら取りにいくのをやめる(tmp_path):
    """**落ちている相手を3,191回叩かない。**"""
    server = _DeadCrtSh()
    ct = _dead_client(tmp_path, server, down_after=5)
    results = ct.prefetch([f"e{i}.example.jp" for i in range(40)])

    assert len(results) == 40, "件数は落とさない（原則4：分母が閉じている）"
    assert server.requests <= 6, (
        f"止まっていない。502 を返す相手に {server.requests} 本投げている"
    )
    assert all(r.error for r in results.values())


def test_やめた理由を残す(tmp_path):
    """**「取れなかった」と「相手が落ちていた」は別である**（原則5）。

    前者は候補が少ない月として読めるが、後者は計測が成立していない。
    """
    server = _DeadCrtSh()
    ct = _dead_client(tmp_path, server, down_after=5)
    ct.prefetch([f"e{i}.example.jp" for i in range(20)])

    assert ct.upstream_down, "落ちていたことが残っていない"
    assert "502" in ct.upstream_down or "http" in ct.upstream_down
    stats = ct.response_stats()
    assert stats["upstream_down"], "manifest に載る形で残っていない"

    # **叩かずに返したものは「観測しなかった」と分かること**
    later = ct.search("zz.example.jp")
    assert later.error_kind == "upstream_down", later.error_kind
    assert later.attempts == 0, "投げていないのに投げた回数が立っている"


def test_1本でも成功していれば止めない(tmp_path):
    """**たまたま重い数件で止めない。** 相手は生きている。

    重いとは**時間切れ**のことである。最初この試験は 502 を返していたが、
    502 は「相手が断った」であって重さではない ── **説明と中身が合って
    いなかった。** 断りの方は別の判定で見る（下の「断りが答えを上回ったら
    止める」）。
    """
    calls = {"n": 0}
    lock = threading.Lock()

    def handle(request: httpx.Request) -> httpx.Response:
        with lock:
            calls["n"] += 1
            n = calls["n"]
        # 1本目だけ成功、あとは全部 時間切れ（重いドメイン）
        if n == 1:
            return httpx.Response(200, json=[{"name_value": "mail.example.jp"}])
        raise httpx.ReadTimeout("too slow", request=request)

    ct = CrtShClient(
        cache_dir=tmp_path / "crtsh",
        month="2026-09",
        qps=0.0,
        retries=0,
        concurrency=1,
        down_after=5,
        client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    ct.prefetch([f"e{i}.example.jp" for i in range(20)])
    assert ct.upstream_down is None, "1本成功しているのに落ちていると判断した"
    assert calls["n"] == 20, "全部投げていない"


def test_キャッシュは止めたあとも返る(tmp_path):
    """**持っているものまで捨てない。**"""
    ok = _SlowCrtSh(delay=0.0, names=["mail.example.jp"])
    warm = CrtShClient(
        cache_dir=tmp_path / "crtsh",
        month="2026-09",
        qps=0.0,
        concurrency=1,
        client=httpx.Client(transport=httpx.MockTransport(ok.handle)),
    )
    warm.search("cached.example.jp")

    server = _DeadCrtSh()
    ct = _dead_client(tmp_path, server, down_after=2)
    ct.prefetch([f"e{i}.example.jp" for i in range(10)])
    assert ct.upstream_down

    got = ct.search("cached.example.jp")
    assert got.from_cache and not got.error, "キャッシュにあるのに返っていない"


def test_設定で止めないようにもできる(tmp_path):
    """0 で無効。**判断は運営者に残す。**"""
    server = _DeadCrtSh()
    ct = _dead_client(tmp_path, server, down_after=0)
    ct.prefetch([f"e{i}.example.jp" for i in range(10)])
    assert ct.upstream_down is None
    assert server.requests == 10, "止めない設定なのに止まっている"


# --------------------------------------------------------------------------
# まだらに落ちているとき
#
# 2026-09-25 の crt.sh は 30% → 17% → 10% と**まだらに**落ちていた。
# 投げ直し2回のおかげで連続失敗が20本に届かず、「丸ごと落ちている」の
# 判定は作動しない ── そのまま回すと、3分の1しか見えていない月ができる。
# --------------------------------------------------------------------------


@pytest.fixture
def no_backoff(monkeypatch):
    """投げ直しの待ちを飛ばす。**待ち方はここで見たいことではない。**

    実物は 2 秒・4 秒と待つので、80ドメインぶん回すと試験が分単位になる。
    """
    from mailauth import ctlog as _ctlog

    monkeypatch.setattr(_ctlog.time, "sleep", lambda _s: None)


class _FlakyCrtSh:
    """N 回に1回だけ 200、残りは 502 を返す crt.sh。"""

    def __init__(self, one_in: int) -> None:
        self.one_in = one_in
        self.lock = threading.Lock()
        self.requests = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        with self.lock:
            self.requests += 1
            n = self.requests
        if n % self.one_in == 0:
            return httpx.Response(200, json=[{"name_value": "mail.example.jp"}])
        return httpx.Response(502, text="502 Bad Gateway")


def _flaky_client(tmp_path, server, *, down_after: int, retries: int = 2) -> CrtShClient:
    return CrtShClient(
        cache_dir=tmp_path / "crtsh",
        month="2026-09",
        qps=0.0,
        retries=retries,
        concurrency=1,
        down_after=down_after,
        client=httpx.Client(transport=httpx.MockTransport(server.handle)),
    )


def test_連続失敗の判定はまだらな相手を捕まえない(tmp_path, no_backoff):
    """**先に、いまの判定が効かないことを確かめる。**

    効かないことを示さずに2つ目を足すと、要らないものを足したことになる。
    """
    server = _FlakyCrtSh(one_in=10)  # 相手が答えるのは10本に1本
    # **2つ目の判定を切って**、1つ目だけで捕まるかを見る
    ct = _flaky_client(tmp_path, server, down_after=0)
    ct.prefetch([f"e{i}.example.jp" for i in range(60)])

    assert ct._succeeded > 0, "1本も成功していないなら、まだらではない"
    assert ct.response_stats()["errors_by_kind"].get("http", 0) > 0
    # down_after=0 なので当然止まらないが、**成功が混ざっている**ことが要点。
    # 「1本も成功していない」という条件は、この相手では永久に満たされない
    assert ct.upstream_down is None


def test_断りが答えを上回ったら止める(tmp_path, no_backoff):
    """**`http` は相手が断ったということ。** ドメインの重さでは起きない。"""
    server = _FlakyCrtSh(one_in=10)
    ct = _flaky_client(tmp_path, server, down_after=20)
    ct.prefetch([f"e{i}.example.jp" for i in range(80)])

    assert ct.upstream_down, "まだら落ちを捕まえていない"
    assert "断られた" in ct.upstream_down
    assert server.requests < 80 * 3, f"止まっていない。{server.requests} 本投げている"


def test_答えの方が多ければ止めない(tmp_path, no_backoff):
    """**たまに 502 が混ざるだけの相手は、正常である。**

    ここで効かせたいのは「断り > 答え」の側なので、**断りが `down_after` に
    届く量は起こす。** 投げ直しを入れると 502 が3回連続せず、ドメイン単位の
    失敗が1件も起きない ── それだと比率の条件を通っていないのに通ったように
    見える（実際そうなっていた。条件を消しても落ちなかった）。
    """

    class _MostlyOk:
        def __init__(self):
            self.lock = threading.Lock()
            self.requests = 0

        def handle(self, request):
            with self.lock:
                self.requests += 1
                n = self.requests
            if n % 3 == 0:
                return httpx.Response(502, text="502")
            return httpx.Response(200, json=[{"name_value": "mail.example.jp"}])

    server = _MostlyOk()
    ct = _flaky_client(tmp_path, server, down_after=20, retries=0)
    ct.prefetch([f"e{i}.example.jp" for i in range(90)])

    kinds = ct.response_stats()["errors_by_kind"]
    assert kinds.get("http", 0) >= 20, (
        f"断りが {kinds.get('http', 0)} 件しか起きておらず、比率の条件を通っていない"
    )
    assert ct._succeeded > kinds["http"], "答えの方が多い状況になっていない"
    assert ct.upstream_down is None, f"正常な相手を落ちていると判断した: {ct.upstream_down}"


def test_時間切ればかりでは止めない(tmp_path, no_backoff):
    """**`timeout` はドメインの重さで起きる。** 相手が断ったのではない。

    run 9 の失敗は実取得の58%だが、その大半は重いドメインの時間切れだった。
    そこで止めては、正常な月が測れなくなる。
    """

    class _FirstOkThenSlow:
        """1本目だけ答え、あとは全部時間切れ。

        **「1本も成功していない」の判定を外した状態**を作るためで、
        ここで見たいのは2つ目の判定が時間切れに反応しないことである。
        """

        def __init__(self):
            self.lock = threading.Lock()
            self.requests = 0

        def handle(self, request):
            with self.lock:
                self.requests += 1
                n = self.requests
            if n == 1:
                return httpx.Response(200, json=[{"name_value": "mail.example.jp"}])
            raise httpx.ReadTimeout("too slow", request=request)

    server = _FirstOkThenSlow()
    ct = CrtShClient(
        cache_dir=tmp_path / "crtsh",
        month="2026-09",
        qps=0.0,
        retries=0,
        concurrency=1,
        down_after=20,
        client=httpx.Client(transport=httpx.MockTransport(server.handle)),
    )
    ct.prefetch([f"e{i}.example.jp" for i in range(60)])

    assert ct._succeeded == 1, "1本も通っていないと、別の判定で止まってしまう"
    assert ct.response_stats()["errors_by_kind"].get("timeout", 0) >= 50
    assert ct.upstream_down is None, f"時間切れだけで止めている: {ct.upstream_down}"
