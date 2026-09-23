"""長い工程の進捗を実行中に出す。

**原則4（件数を必ず記録する）は manifest で満たしているが、それは工程が
終わったあとの話である。** P2 の CT ログ取得と P4 の DNS 計測は数時間
かかることがあり、その間、実行ログには何も出ない。

実際に困った。2026-09 の国内計測で P2 が4時間半動き続け、「何割まで進んだか」
も「あと何時間か」も分からなかった。GitHub Actions の API は**実行中の
ジョブのログを返さない**（404）ので、外から覗くこともできない。
止めるべきか待つべきかを判断する材料が無い状態だった。

## 標準エラー出力に出す

標準出力は manifest の要約が使っており、そちらは機械が読む。進捗は人が
読むものなので混ぜない。GitHub Actions は stderr もログに出すので、
ブラウザで実行中のジョブを開けば流れていく。

## 時間で間引く

件数で間引くと（「100件ごと」）、1件あたりの所要が大きくぶれる処理では
役に立たない。crt.sh は速い相手なら1秒、遅い相手なら3分かかる。
**間隔は時間で決める。**

## 残り時間は「これまでと同じ調子なら」としか言えない

平均から外挿するが、その前提は弱い。大企業ほど CT ログの応答が重く、
処理順はシャッフルしてあるので偏りは均されるが、それでも予測でしかない。
**断定的に書かない。**
"""

from __future__ import annotations

import sys
import time
from typing import TextIO

#: 出力の最小間隔（秒）。短すぎるとログが進捗で埋まる
DEFAULT_INTERVAL_SEC = 30.0


def _hms(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}時間{m:02d}分"
    if m:
        return f"{m}分{s:02d}秒"
    return f"{s}秒"


class Progress:
    """処理の進み具合を一定間隔で流す。

    `tick()` を毎件呼んでよい。実際に出力するかどうかはこの中で決める。
    """

    def __init__(
        self,
        total: int,
        label: str,
        *,
        interval_sec: float = DEFAULT_INTERVAL_SEC,
        stream: TextIO | None = None,
        clock=time.monotonic,
    ) -> None:
        self.total = max(int(total), 0)
        self.label = label
        self.interval = float(interval_sec)
        self.stream = stream if stream is not None else sys.stderr
        self._clock = clock
        self._start = clock()
        self._last = self._start
        self.done = 0
        #: 付随して数えたいもの（キャッシュ命中など）
        self.counters: dict[str, int] = {}

    def tick(self, n: int = 1, **counters: int) -> bool:
        """1件進める。実際に出力したら True。"""
        self.done += n
        for k, v in counters.items():
            self.counters[k] = self.counters.get(k, 0) + v

        now = self._clock()
        # **最後の1件は間隔によらず必ず出す。** 終わったことが分かる
        is_last = self.total and self.done >= self.total
        if not is_last and now - self._last < self.interval:
            return False
        self._last = now
        self._emit(now)
        return True

    def _emit(self, now: float) -> None:
        elapsed = now - self._start
        parts = [f"[{self.label}]"]

        if self.total:
            pct = self.done / self.total * 100
            parts.append(f"{self.done}/{self.total} ({pct:.1f}%)")
        else:
            parts.append(f"{self.done} 件")

        parts.append(f"経過 {_hms(elapsed)}")

        if self.done:
            per = elapsed / self.done
            parts.append(f"1件 {per:.1f}秒")
            if self.total and self.done < self.total:
                remain = per * (self.total - self.done)
                # **断定しない。** 1件あたりの所要は相手によって桁が違う
                parts.append(f"この調子なら残り {_hms(remain)}")

        if self.counters:
            detail = " ".join(f"{k}={v}" for k, v in sorted(self.counters.items()))
            parts.append(f"（{detail}）")

        print("  " + " ".join(parts), file=self.stream, flush=True)

    def finish(self) -> None:
        """打ち切られた場合も含めて、最後に1行出す。"""
        self._emit(self._clock())
