"""P3 / P4 の DNS 問い合わせを並行にする。

2026-09 の国内計測で、P4 が1ドメイン 7.7秒・実効 7.5 qps しか出ず、
2,153ドメインで4時間36分かかってジョブの上限に当たった。1ドメイン約58本を
1本ずつ引いており、**上流への往復（約130ミリ秒）がそのまま積み上がる。**
設定の `qps: 80` は一度も効いていない。

ここで確かめるのは3つで、**どれか1つでも欠けると意味が変わる。**

  1. **権威DNSへの本数が増えていないこと**（投げる間隔は全スレッド共有）
  2. **応答待ちが実際に重なっていること**（重ならないなら直列と同じ）
  3. **出力の並びが実行ごとに変わらないこと**（原則6）

1 を落とすと、速くする代わりに相手に迷惑をかけたことになる。qps を絞って
いるのは礼儀であって性能の都合ではない。3 を落とすと、同じ入力から違う
並びが出て、差分を取って中身を比べられなくなる。
"""

from __future__ import annotations

import threading
import time

import pandas as pd
import pytest

from mailauth.contracts import DOMAIN_ARROW_SCHEMA
from mailauth.io import write_parquet
from mailauth.p4_measure import run as run_p4
from mailauth.p4_measure.bronze import iter_bronze_files, read_bronze
from mailauth.paths import bronze_dir, phase_output
from mailauth.resolver import DnsAnswer, DnsResolver

RUN = "2026-08"


class _SlowBackend:
    """応答に時間がかかる DNS を模す。ネットワークには出ない。

    いつ投げられたか、同時に何本飛んでいたかを記録する。
    """

    name = "slow"
    version = "test"
    resolver_label = "slow->static"

    def __init__(self, delay: float = 0.02, delay_for=None) -> None:
        self.delay = delay
        #: ドメインごとに遅さを変えたいとき。**共有状態を書き換えない**
        self.delay_for = delay_for
        self.lock = threading.Lock()
        self.issued: list[float] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.stats = {"queries": 0, "cache_hits": 0, "tcp_failed": 0}

    def query(self, query):
        with self.lock:
            self.issued.append(time.monotonic())
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            self.stats["queries"] += 1
        try:
            time.sleep(self.delay_for(query.name) if self.delay_for else self.delay)
        finally:
            with self.lock:
                self.in_flight -= 1
        return DnsAnswer(
            name=query.name,
            rtype=query.rtype,
            observed=True,
            record_present=False,
            rcode="NODATA",
        )


def _write_domains(tmp_path, n: int) -> list[str]:
    rows = [
        {
            "domain_id": f"d:{i:03d}",
            "entity_id": "jp:1",
            "domain": f"d{i:03d}.example.jp",
            "measure_tier": "C",
            "is_measured": True,
        }
        for i in range(n)
    ]
    write_parquet(
        [
            {**r, **{f.name: None for f in DOMAIN_ARROW_SCHEMA if f.name not in r}}
            for r in rows
        ],
        phase_output(RUN, "p3_domains", "domains.parquet"),
        DOMAIN_ARROW_SCHEMA,
    )
    return [r["domain"] for r in rows]


def _measured_domains(run_id: str) -> list[str]:
    """bronze に並んだ順のドメイン（重複を潰さず、最初に出た順）。"""
    seen: list[str] = []
    for path in iter_bronze_files(bronze_dir(run_id)):
        for record in read_bronze(path):
            if not seen or seen[-1] != record["domain"]:
                seen.append(record["domain"])
    return seen


# --------------------------------------------------------------------------
# 1. 権威DNSへの本数は増えていない
# --------------------------------------------------------------------------


def test_投げる間隔は全スレッドで共有される():
    """**qps を緩めずに速くする**のが目的なので、ここが本題。

    スレッドごとに間隔を持つと、実効 qps が本数倍になる。それは
    「並行にした」ではなく「絞りを外した」である。
    """
    qps = 200.0  # 試験を待たせないための値。間隔は 0.005 秒
    interval = 1.0 / qps
    resolver = DnsResolver(qps=qps, cache=False)
    issued: list[float] = []
    lock = threading.Lock()

    def issue() -> None:
        for _ in range(8):
            resolver._throttle()
            with lock:
                issued.append(time.monotonic())

    started = time.monotonic()
    threads = [threading.Thread(target=issue) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.monotonic() - started

    assert len(issued) == 32
    # **全体で見る。** 1本ごとの時刻はスレッドの切り替わりで前後しうるが、
    # 間隔を共有しているなら N 本を投げ終えるまでに (N-1) 回ぶんの間隔が要る。
    # スレッドごとに持っていると本数ぶん短くなる（4本なら約 1/4）
    floor = (len(issued) - 1) * interval
    assert elapsed >= floor * 0.9, (
        f"{len(issued)} 本を {elapsed:.3f} 秒で投げ終えている"
        f"（間隔 {interval} 秒を守るなら最低 {floor:.3f} 秒）。"
        "スレッドごとに間隔を持っていないか"
    )


def test_並行にしても総クエリ数は変わらない(tmp_path, monkeypatch):
    monkeypatch.setenv("MAILAUTH_DATA_ROOT", str(tmp_path))
    _write_domains(tmp_path, 12)

    serial = _SlowBackend(delay=0)
    run_p4(run_id=RUN, backend=serial, concurrency=1)
    n_serial = serial.stats["queries"]

    monkeypatch.setenv("MAILAUTH_DATA_ROOT", str(tmp_path / "b"))
    _write_domains(tmp_path, 12)
    parallel = _SlowBackend(delay=0)
    run_p4(run_id=RUN, backend=parallel, concurrency=4)

    assert parallel.stats["queries"] == n_serial, (
        f"直列 {n_serial} 本に対し並行 {parallel.stats['queries']} 本。"
        "並行にしただけで本数が変わってはいけない"
    )


# --------------------------------------------------------------------------
# 2. 応答待ちが実際に重なっている
# --------------------------------------------------------------------------


def test_応答待ちが重なる(tmp_path, monkeypatch):
    """重ならないなら直列と同じで、作った意味が無い。"""
    monkeypatch.setenv("MAILAUTH_DATA_ROOT", str(tmp_path))
    _write_domains(tmp_path, 12)
    backend = _SlowBackend(delay=0.02)
    run_p4(run_id=RUN, backend=backend, concurrency=4)
    assert backend.max_in_flight > 1, "同時に1本しか飛んでいない。直列のまま"


def test_同時本数の上限を超えない(tmp_path, monkeypatch):
    monkeypatch.setenv("MAILAUTH_DATA_ROOT", str(tmp_path))
    _write_domains(tmp_path, 16)
    backend = _SlowBackend(delay=0.02)
    run_p4(run_id=RUN, backend=backend, concurrency=3)
    assert backend.max_in_flight <= 3, f"同時 {backend.max_in_flight} 本（上限 3）"


def test_直列より速い(tmp_path, monkeypatch):
    """実時間が縮んでいること。**これが目的そのもの。**"""
    monkeypatch.setenv("MAILAUTH_DATA_ROOT", str(tmp_path / "a"))
    _write_domains(tmp_path, 8)
    t0 = time.monotonic()
    run_p4(run_id=RUN, backend=_SlowBackend(delay=0.02), concurrency=1)
    serial = time.monotonic() - t0

    monkeypatch.setenv("MAILAUTH_DATA_ROOT", str(tmp_path / "b"))
    _write_domains(tmp_path, 8)
    t0 = time.monotonic()
    run_p4(run_id=RUN, backend=_SlowBackend(delay=0.02), concurrency=4)
    parallel = time.monotonic() - t0

    assert parallel < serial * 0.7, f"直列 {serial:.3f}秒 に対し並行 {parallel:.3f}秒"


# --------------------------------------------------------------------------
# 3. 出力の並びが実行ごとに変わらない（原則6）
# --------------------------------------------------------------------------


def test_bronzeの並びは完了順ではなく入力順(tmp_path, monkeypatch):
    """**後ろのドメインほど速く返るようにしても、順番が入れ替わらないこと。**

    完了順に書くと、同じ入力から違う並びの bronze が出る。差分を取って
    中身を比べることができなくなる。
    """

    def uneven(name: str) -> float:
        # d000 が最も遅く、d011 が最も速い
        digits = "".join(c for c in name.split(".")[0] if c.isdigit())
        return 0.03 - int(digits or 0) * 0.002

    monkeypatch.setenv("MAILAUTH_DATA_ROOT", str(tmp_path / "a"))
    _write_domains(tmp_path, 12)
    run_p4(run_id=RUN, backend=_SlowBackend(delay_for=uneven), concurrency=4)
    parallel = _measured_domains(RUN)

    monkeypatch.setenv("MAILAUTH_DATA_ROOT", str(tmp_path / "b"))
    _write_domains(tmp_path, 12)
    run_p4(run_id=RUN, backend=_SlowBackend(delay=0), concurrency=1)
    serial = _measured_domains(RUN)

    assert parallel == serial, (
        "bronze の並びが直列のときと違う。完了順に書いていないか\n"
        f"  並行: {parallel[:5]}\n  直列: {serial[:5]}"
    )


def test_並行にしても集計が変わらない(tmp_path, monkeypatch):
    """原則4（何件処理して何件失敗したか）の数字が並行で狂わないこと。"""
    monkeypatch.setenv("MAILAUTH_DATA_ROOT", str(tmp_path / "a"))
    _write_domains(tmp_path, 12)
    serial = run_p4(run_id=RUN, backend=_SlowBackend(delay=0), concurrency=1)

    monkeypatch.setenv("MAILAUTH_DATA_ROOT", str(tmp_path / "b"))
    _write_domains(tmp_path, 12)
    parallel = run_p4(run_id=RUN, backend=_SlowBackend(delay=0), concurrency=4)

    for key in ("input", "success", "failed", "skipped"):
        assert serial["counts"][key] == parallel["counts"][key], (
            f"counts.{key} が直列 {serial['counts'][key]} / "
            f"並行 {parallel['counts'][key]} で違う"
        )
    for key in ("by_rcode", "by_purpose", "queries_total"):
        assert serial["breakdown"][key] == parallel["breakdown"][key], (
            f"breakdown.{key} が直列と並行で違う"
        )


# --------------------------------------------------------------------------
# 組み込まれていること
# --------------------------------------------------------------------------


#: 共有状態に触ってよい関数。**ここ以外に錠の無い経路を作らない。**
_SHARED_STATE_METHODS = ("_cache_get", "_cache_put", "_bump")


def test_リゾルバの共有状態は出入口が絞られている():
    """**錠を1か所忘れても、少ない件数では落ちない。** 検査で止める。

    「どこかに `_state_lock` がある」では足りない ── キャッシュだけ錠の外に
    出しても、その検査は通ってしまう。**触る場所そのものを絞る。**

    間隔・キャッシュ・統計の3つで、どれを外しても壊れ方が違う
    （実効 qps が上がる / 取りこぼす / 件数が狂う）。
    """
    import inspect
    import re

    from mailauth.resolver import DnsResolver

    # 投げる間隔
    throttle = inspect.getsource(DnsResolver._throttle)
    assert "with self._throttle_lock" in throttle, (
        "錠を取らずに間隔を更新している。スレッドごとに間隔を持つのと同じ"
    )
    assert re.search(r"with self\._throttle_lock:(?:.|\n)*?time\.sleep", throttle), (
        "錠の外で眠っている。全スレッドが同時に目を覚まして一斉に投げる"
    )

    # 共有状態は3つの出入口の中だけ。**それぞれが錠を取っていること**
    for name in _SHARED_STATE_METHODS:
        body = inspect.getsource(getattr(DnsResolver, name))
        assert "with self._state_lock" in body, f"{name} が錠を取っていない"

    allowed = {
        line
        for name in _SHARED_STATE_METHODS
        for line in inspect.getsource(getattr(DnsResolver, name)).splitlines()
    }
    stray = [
        line.strip()
        for line in inspect.getsource(DnsResolver).splitlines()
        if ("self._cache[" in line or "self._cache.get" in line
            or "self._cache.setdefault" in line or "self.stats[" in line)
        and line not in allowed
        and not line.lstrip().startswith("#")
    ]
    assert not stray, (
        "共有状態に、出入口の外から触っている行がある:\n  " + "\n  ".join(stray)
    )


@pytest.mark.parametrize(
    "module, chunk_name",
    [
        ("mailauth.p4_measure.runner", "MEASURE_CHUNK"),
        ("mailauth.p3_domains.runner", "PROBE_CHUNK"),
    ],
)
def test_塊の大きさが同時本数より十分に大きい(module, chunk_name):
    """塊の終わりで待ち合わせるので、同時本数と同程度だと並行が利かない。"""
    import importlib

    mod = importlib.import_module(module)
    assert getattr(mod, chunk_name) >= mod.DEFAULT_CONCURRENCY * 4


@pytest.mark.parametrize(
    "module, func",
    [
        ("mailauth.p4_measure.runner", "_in_input_order"),
        ("mailauth.p3_domains.runner", "_probe_in_order"),
    ],
)
def test_工程が並行取得を使っている(module, func):
    """**外すと元の所要時間に戻る。**

    直列に戻したことは結果を見ても分からない（同じ数字が出る）。
    遅いだけなので、検査で止める。
    """
    import importlib
    import inspect

    mod = importlib.import_module(module)
    # **定義ではなく呼び出しを見る。** `def _probe_in_order(` も
    # `_probe_in_order(` に一致するので、本体だけ見ると
    # 「仕組みはあるが呼んでいない」を見逃す
    called = inspect.getsource(mod.run)
    assert f"{func}(" in called, (
        f"{module} の run() が {func} を呼んでいない。"
        "仕組みだけ残して直列に戻すと、結果は同じで遅いだけになる"
    )
    assert "ThreadPoolExecutor" in inspect.getsource(mod), (
        f"{module} に待ち合わせの仕組みが無い"
    )


def test_設定に同時本数がある():
    """設定から変えられること（原則7）。**読まれないキーを置かない。**"""
    from mailauth.config import load_measure_config, load_yaml

    assert int(load_measure_config()["rate"]["concurrency"]) >= 1
    assert int(load_yaml("configs/candidates.yaml")["resolver"]["concurrency"]) >= 1


def test_同時本数は1を下回らない(tmp_path, monkeypatch):
    """0 や負を入れても直列に落ちるだけで、止まらないこと。"""
    monkeypatch.setenv("MAILAUTH_DATA_ROOT", str(tmp_path))
    _write_domains(tmp_path, 3)
    backend = _SlowBackend(delay=0)
    run_p4(run_id=RUN, backend=backend, concurrency=0)
    assert backend.max_in_flight == 1
    assert pd is not None  # 読み込みの確認（parquet 経由で読んでいる）
