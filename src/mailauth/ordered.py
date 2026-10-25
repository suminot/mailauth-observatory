"""応答待ちだけを重ね、結果は入力順に返す（P3 / P4 で共用）。

**塊で区切って待ち合わせない。** 以前は64件ずつ `pool.map` に渡して
いて、塊の中に遅いドメインが1つあると、残りの作業者は塊が終わるまで
手が空いたまま待っていた。

2026-10 の米国計測（run 40150006097）の P4 で実際に起きた。走っている
間は設定の上限（80 qps）近くまで出ていたのに、**4分前後止まっては
30件ほど進む**を繰り返し、平均は1ドメイン7.6秒だった。3,298ドメインの
うち1,071ドメインでジョブの時間上限に当たった。

ここでは**窓を滑らせる。** 先頭が終わって返すたびに1件足す。先頭が
遅くても、窓の中の後ろの件は作業者が進め続ける。手が止まるのは、
窓いっぱいまで終わってなお先頭が返らないときだけである。
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from itertools import islice
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


def in_input_order(
    fn: Callable[[T], R],
    items: Iterable[T],
    *,
    concurrency: int,
    window: int,
) -> Iterator[R]:
    """`fn` を並行に呼び、**結果は入力順に返す。**

    完了順に返すと、同じ入力から出力の並びが変わる（原則6）。

    `window` は「返していない件数」の上限である。**全件を一度に投げない**
    ── P4 は1ドメイン約60件の応答を抱えるので、30,000ドメインを一度に
    持つとメモリに乗らない。同時本数より十分大きくしないと、先頭が
    遅いたびに手が止まる。
    """
    if concurrency <= 1:
        # **並行にしない経路を残す。** 注入したバックエンドで測る検査や、
        # 1件ずつ追いたいときに使う
        for item in items:
            yield fn(item)
        return

    window = max(int(window), concurrency)
    pool = ThreadPoolExecutor(max_workers=concurrency)
    pending: deque[Future[R]] = deque()
    source = iter(items)
    try:
        for item in islice(source, window):
            pending.append(pool.submit(fn, item))
        while pending:
            result = pending.popleft().result()
            # **返す前に次を足す。** 呼び出し側が書き出している間も
            # 作業者を遊ばせない
            for item in islice(source, 1):
                pending.append(pool.submit(fn, item))
            yield result
    finally:
        # 呼び出し側が途中でやめた（例外・時間切れ）なら、まだ始まって
        # いない分は投げない
        for future in pending:
            future.cancel()
        pool.shutdown(wait=True)
