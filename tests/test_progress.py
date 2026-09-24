"""長い工程の進捗表示。

**原則4（件数を必ず記録する）は manifest で満たしているが、それは工程が
終わったあとの話である。** 2026-09 の国内計測で P2 が4時間半動き続け、
何割まで進んだかも残り時間も分からなかった（GitHub Actions の API は
実行中のジョブのログを返さない）。止めるか待つかの判断材料が無かった。
"""

from __future__ import annotations

import io

from mailauth.progress import Progress


class FakeClock:
    """時間で間引く挙動を、実時間を待たずに確かめる。"""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _p(total=100, interval=30.0):
    clock = FakeClock()
    out = io.StringIO()
    return Progress(total, "試験", interval_sec=interval, stream=out, clock=clock), out, clock


def test_間隔の内側では出さない():
    """毎件出すとログが進捗で埋まる。"""
    p, out, clock = _p()
    for _ in range(10):
        clock.advance(1)
        p.tick()
    assert out.getvalue() == ""


def test_間隔を越えたら出す():
    p, out, clock = _p()
    clock.advance(31)
    assert p.tick() is True
    assert "試験" in out.getvalue()
    assert "1/100" in out.getvalue()


def test_最後の1件は間隔によらず出す():
    """終わったことが分からないと、止まったのか終わったのか区別できない。"""
    p, out, clock = _p(total=3)
    p.tick()
    p.tick()
    assert out.getvalue() == ""
    assert p.tick() is True
    assert "3/3 (100.0%)" in out.getvalue()


def test_件数ではなく時間で間引く():
    """1件あたりの所要が桁で違う処理では、件数での間引きは役に立たない。

    crt.sh は速い相手なら1秒、遅い相手なら3分かかる。
    """
    p, out, clock = _p(total=1000, interval=30.0)
    for _ in range(500):
        clock.advance(0.01)  # 速い相手が500件続いても
        p.tick()
    assert out.getvalue() == "", "件数で出していると、ここで何度も出てしまう"

    clock.advance(30)
    p.tick()
    assert out.getvalue().count("\n") == 1


def test_残り時間は断定しない():
    """1件あたりの所要は相手によって桁が違うので、予測でしかない。"""
    p, out, clock = _p(total=100)
    clock.advance(60)
    p.tick()
    text = out.getvalue()
    assert "この調子なら残り" in text
    assert "経過" in text


def test_終わっていれば残り時間を出さない():
    p, out, clock = _p(total=2)
    clock.advance(10)
    p.tick()
    clock.advance(10)
    p.tick()
    last = out.getvalue().strip().splitlines()[-1]
    assert "残り" not in last


def test_付随する数を一緒に出せる():
    """キャッシュが効いているかは、終わってから知っても遅い。"""
    p, out, clock = _p(total=10)
    p.tick(キャッシュ=1)
    clock.advance(31)
    p.tick(キャッシュ=1, 取得=1)
    text = out.getvalue()
    assert "キャッシュ=2" in text
    assert "取得=1" in text


def test_総数が分からなくても件数は出す():
    p, out, clock = _p(total=0)
    clock.advance(31)
    p.tick()
    assert "1 件" in out.getvalue()


def test_打ち切られても最後に1行出す():
    """上限に達して break したときも、どこまで進んだかを残す。"""
    p, out, clock = _p(total=100)
    for _ in range(5):
        clock.advance(1)
        p.tick()
    assert out.getvalue() == ""
    p.finish()
    assert "5/100" in out.getvalue()


def test_終わっていれば最後の行を二度出さない():
    """**完了した工程が毎回二重に出ていた。**

    最後の1件は `tick()` が必ず出す。そのあと `finish()` が同じ件数を
    もう一度出すと、経過時間だけが違う行が並ぶ ── `finish()` を呼ぶのは
    後続の処理が終わったあとなので、実際に「経過22秒」の直後に
    「経過33秒」が出ていた。**どちらが取得にかかった時間なのか読めない。**
    """
    p, out, clock = _p(total=3)
    for _ in range(3):
        clock.advance(1)
        p.tick()
    assert out.getvalue().count("\n") == 1, "最後の1件が出ていない"

    clock.advance(60)  # 後続の処理で時間が経ってから finish が呼ばれる
    p.finish()
    assert out.getvalue().count("\n") == 1, "同じ件数の行を二度出している"


def test_一度も出していなければ最後に出す():
    """0件で終わった工程も「0件だった」と分かる必要がある。"""
    p, out, _ = _p(total=0)
    p.finish()
    assert "0 件" in out.getvalue()


def test_打ち切りのあと更に進めばまた出す():
    p, out, clock = _p(total=100)
    p.tick()
    p.finish()
    lines = out.getvalue().count("\n")
    clock.advance(1)
    p.tick()
    p.finish()
    assert out.getvalue().count("\n") == lines + 1


def test_標準出力を汚さない():
    """標準出力は manifest の要約が使う。**機械が読む側に混ぜない。**"""
    import inspect

    from mailauth import progress

    source = inspect.getsource(progress)
    assert "sys.stderr" in source
    assert "sys.stdout" not in source


def test_長い工程に組み込まれている():
    """P2 と P4 は数時間かかる。**入れ忘れると同じ目に遭う。**"""
    import inspect

    from mailauth.p2_candidates import runner as p2
    from mailauth.p4_measure import runner as p4

    for mod, name in ((p2, "P2"), (p4, "P4")):
        source = inspect.getsource(mod)
        assert "Progress(" in source, f"{name} に進捗表示が無い"
        assert ".tick(" in source, f"{name} が tick を呼んでいない"
