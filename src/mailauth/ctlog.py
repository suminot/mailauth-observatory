"""Certificate Transparency ログからのドメイン発見（crt.sh）。

crt.sh は重い。レート制限とリトライを必ず入れ、結果は run 単位で
キャッシュする（DESIGN.md P2 実装メモ）。

CT ログは1ドメインあたり数百〜数千の証明書を返すことがある。
eTLD+1 に正規化してから重複排除しないと候補が爆発する。

## キャッシュは月で区切る

**CT ログは追記されていく。** 先月の応答を今月の観測として使うと、
その間に発行された証明書が永久に見えない。新しく作られたドメインは
CT 経由でしか見つからないことがあるので、**候補生成が初月の状態で
凍結する。** しかも数字は動かないだけなので気付けない。

そこでキャッシュを `month=YYYY-MM/` で区切る。

  - 同じ月の再実行はキャッシュを使う（原則6 冪等、crt.sh に再負荷をかけない）
  - 月が変われば取り直す（新しい証明書が入る）

`month` を渡さない場合は月で区切らない。テストと単発の調査用で、
**月次計測の経路では必ず渡す。**

## qps は「投げる間隔」であって「同時本数」ではない

2026-09 の国内計測で P2 が5時間17分走っても終わらず、ジョブの上限で
打ち切られた。`qps: 0.5` にしているので2秒に1本しか投げない計算だが、
**実測の1件あたりは9.5秒以上あった。** 直列に呼んでいるため、応答を
待っている7秒以上のあいだ何もしていない。相手に遠慮しているのではなく、
ただ待っているだけの時間である。

そこで `prefetch()` を置いた。**投げる間隔は 0.5 qps のまま全スレッドで
共有し、応答待ちだけを重ねる。** crt.sh から見た単位時間あたりの本数は
変わらない ── 礼儀として絞っている qps を緩めたわけではない。
変わるのは、こちらが待っている時間だけである。

  直列  ── 投げる 待つ9.5秒 投げる 待つ9.5秒 …
  並行  ── 投げる 投げる 投げる 投げる（2秒間隔）… 応答は重なって返る

`concurrency` 本を超えては走らせない。多くすれば無条件に速くなるもの
ではなく、**qps が下限**になる。

**測る場所で答えが変わる。** 下の2つは条件が違うので、片方だけ読むと
誤る。

  開発機から（2026-09-24、8〜12ドメイン）
    直列     1件 9.1秒
    同時4本  1件 2.4秒
    同時8本  1件 2.3秒   ← ここでは4本で下限（1件2.0秒）に届いている

  GitHub Actions から（2026-09、run 35963788831、新規1,005件）
    同時4本  1件 16.3秒  ← 下限の8倍。**届いていない**

crt.sh は Actions の IP 帯に対して遅い。同時4本で1件16.3秒ということは、
応答1本あたり約65秒かかっている。下限に届かせるには同時30本前後が要る
計算になるが、**上げる判断はまだしない** ── 同じ IP から数時間続けて叩く
ほど遅くなっている可能性があり（失敗率が序盤4%→終盤25%）、その場合は
本数を増やすのは逆効果である。P2 の manifest（`breakdown.ct_response`）に
応答時間の分布を残すようにしたので、次の実行の数字で判断する。
"""

from __future__ import annotations

import json
import statistics
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

from .normalize import etld_plus_one

CRTSH_ENDPOINT = "https://crt.sh/"
USER_AGENT = "mailauth-observatory/0.1 (research; contact via github.com/suminot)"


@dataclass
class CtResult:
    domain: str
    #: 見つかった eTLD+1 の集合
    found: list[str] = field(default_factory=list)
    #: 正規化前に見た FQDN の件数。爆発の度合いを記録する
    raw_names: int = 0
    from_cache: bool = False
    #: キャッシュを使った場合、それがどの月のものか。
    #: **当月以外なら「今月の観測」ではない**（原則5）
    cache_month: str | None = None
    error: str | None = None
    #: 失敗の種類。**「失敗」の一語にまとめない。**
    #: timeout / http / transport / decode で手当てが違う
    #: （時間切れなら待ち方、HTTP なら頼み方を変えることになる）
    error_kind: str | None = None
    #: 実際に応答が返るまでの秒数。キャッシュに当たった場合は None
    duration_sec: float | None = None
    #: 投げ直した回数。0 なら一発で返っている
    attempts: int = 1


#: 同時に応答を待つ本数の既定値。**投げる間隔（qps）とは別のつまみ。**
#: 増やしても qps より速くはならない
DEFAULT_CONCURRENCY = 4


class CtSource(Protocol):
    def search(self, domain: str) -> CtResult: ...

    def prefetch(
        self,
        domains: Iterable[str],
        *,
        on_result: Callable[[str, CtResult], None] | None = None,
    ) -> dict[str, CtResult]:
        """まとめて取る。`search()` と同じ結果を domain 別に返す。"""
        ...


class CrtShClient:
    """crt.sh の JSON 出力を使う。

    identity 検索（`?q=%.<domain>&output=json`）で SAN を含む証明書を引き、
    name_value（改行区切りの FQDN 群）から eTLD+1 を取り出す。
    """

    def __init__(
        self,
        *,
        cache_dir: Path | None = None,
        month: str | None = None,
        qps: float = 0.5,
        timeout: float = 60.0,
        retries: int = 2,
        concurrency: int = DEFAULT_CONCURRENCY,
        client: httpx.Client | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        #: キャッシュを区切る月（`YYYY-MM`）。**月次計測では必ず渡す。**
        #: 渡さないと先月の応答を今月の観測として使ってしまう
        self.month = month
        self.min_interval = 1.0 / qps if qps > 0 else 0.0
        self.timeout = timeout
        self.retries = retries
        #: 同時に応答を待つ本数。**qps とは別物**（モジュール冒頭参照）
        self.concurrency = max(int(concurrency), 1)
        self._client = client
        self._last_call = 0.0
        # **投げる間隔は全スレッドで共有する。** スレッドごとに持つと、
        # 本数ぶんだけ crt.sh への実効 qps が上がってしまう
        self._throttle_lock = threading.Lock()
        # 応答時間と失敗の内訳。**prefetch は複数スレッドから書く**ので
        # 間隔の錠とは別の錠で守る（同じ錠を使うと、記録のたびに投げる
        # 権利を奪い合うことになる）
        self._stats_lock = threading.Lock()
        self._timings: list[float] = []
        self._error_kinds: dict[str, int] = {}
        self._retried = 0

    def _throttle(self) -> None:
        """次の1本を投げてよい時刻まで待つ。**全スレッドで1つの間隔。**

        ロックを保ったまま眠る。こうすると「投げる瞬間」だけが直列になり、
        応答を待つ時間は重なる。ロックの外で眠ると全スレッドが同じ時刻に
        目を覚まして一斉に投げるので、絞っている意味が無くなる。
        """
        if self.min_interval <= 0:
            return
        with self._throttle_lock:
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()

    def _record(self, elapsed: float, *, kind: str | None, attempts: int) -> None:
        """1本ぶんの応答時間と結果を控える。**worker スレッドから呼ばれる。**"""
        with self._stats_lock:
            self._timings.append(elapsed)
            if kind:
                self._error_kinds[kind] = self._error_kinds.get(kind, 0) + 1
            if attempts > 1:
                self._retried += 1

    def response_stats(self) -> dict[str, object]:
        """応答時間の分布と失敗の内訳。**manifest に載せて次の判断に使う。**

        2026-09 の実行では、P2 の実時間の大半が crt.sh の待ちだった可能性が
        あるのに、残っていたのは `errors` の件数だけだった。**時間切れと
        HTTP エラーの区別も、どれだけ待たされたかも無い。** 判断材料が
        無いまま調整すると、次の実行も5時間かけて同じことを学ぶ。

        取れた本数が0なら、0 で埋めずに**空を返す**（原則5：測っていない
        ことと 0 秒だったことは別）。
        """
        with self._stats_lock:
            timings = sorted(self._timings)
            kinds = dict(sorted(self._error_kinds.items()))
            retried = self._retried
        if not timings:
            return {"requests": 0, "note": "crt.sh に1本も投げていない"}

        def pct(q: float) -> float:
            idx = min(int(q * len(timings)), len(timings) - 1)
            return round(timings[idx], 3)

        return {
            "requests": len(timings),
            # 中央値は statistics.median に合わせる（P2 の他の分布と同じ流儀）
            "p50_sec": round(statistics.median(timings), 3),
            "p90_sec": pct(0.90),
            "p99_sec": pct(0.99),
            "max_sec": round(timings[-1], 3),
            "mean_sec": round(sum(timings) / len(timings), 3),
            # **時間切れは待ち方、HTTP は頼み方。** 一語にまとめない
            "errors_by_kind": kinds,
            "retried": retried,
            "timeout_sec": self.timeout,
        }

    def _cache_path(self, domain: str) -> Path | None:
        """キャッシュの置き場所。**月で区切る。**

        月を跨いだ再利用は「先月の観測を今月として出す」ことになるので、
        パスの階層で分けて物理的に起こらないようにする。
        """
        if self.cache_dir is None:
            return None
        safe = domain.replace("/", "_")
        base = self.cache_dir / f"month={self.month}" if self.month else self.cache_dir
        return base / f"{safe}.json"

    def search(self, domain: str, *, client: httpx.Client | None = None) -> CtResult:
        """1ドメイン分を取る。

        `client` を渡すと接続を使い回す。`prefetch()` が本数分の接続を
        張り直さないために使う（`httpx.Client` はスレッド安全）。
        """
        cached = self._cache_path(domain)
        if cached and cached.is_file():
            try:
                payload = json.loads(cached.read_text(encoding="utf-8"))
                return CtResult(
                    domain=domain,
                    found=payload.get("found", []),
                    raw_names=payload.get("raw_names", 0),
                    from_cache=True,
                    cache_month=payload.get("month"),
                )
            except (json.JSONDecodeError, OSError):
                pass

        given = client or self._client
        client = given or httpx.Client(timeout=self.timeout, follow_redirects=True)
        close = given is None
        attempt = 0
        started = time.monotonic()

        def failed(kind: str, message: str) -> CtResult:
            """失敗も**どれだけ待たされたか**を残す。

            時間切れと HTTP エラーでは手当てが違う。件数だけ数えていると、
            「待ち方を変えるべきか」「頼み方を変えるべきか」が分からない。
            """
            elapsed = time.monotonic() - started
            self._record(elapsed, kind=kind, attempts=attempt + 1)
            return CtResult(
                domain=domain,
                error=message,
                error_kind=kind,
                duration_sec=round(elapsed, 3),
                attempts=attempt + 1,
            )

        try:
            while True:
                self._throttle()
                try:
                    resp = client.get(
                        CRTSH_ENDPOINT,
                        params={"q": f"%.{domain}", "output": "json"},
                        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                    )
                except httpx.TimeoutException as exc:
                    # **時間切れは別に数える。** 相手が遅いだけなら、
                    # 待ち時間を延ばす方が投げ直すより成功率が上がる
                    if attempt < self.retries:
                        attempt += 1
                        time.sleep(2 ** attempt)
                        continue
                    return failed("timeout", f"timeout: {exc}"[:200])
                except httpx.HTTPError as exc:
                    if attempt < self.retries:
                        attempt += 1
                        time.sleep(2 ** attempt)
                        continue
                    return failed("transport", f"request failed: {exc}"[:200])

                if resp.status_code in (429, 502, 503, 504) and attempt < self.retries:
                    attempt += 1
                    time.sleep(2 ** attempt)
                    continue
                if resp.status_code != 200:
                    return failed("http", f"HTTP {resp.status_code}")
                try:
                    rows = resp.json()
                except ValueError:
                    return failed("decode", "JSON として解釈できない応答")
                break
        finally:
            if close:
                client.close()

        elapsed = time.monotonic() - started
        self._record(elapsed, kind=None, attempts=attempt + 1)

        result = _extract(domain, rows)
        result.cache_month = self.month
        result.duration_sec = round(elapsed, 3)
        result.attempts = attempt + 1
        if cached:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(
                json.dumps(
                    {
                        "found": result.found,
                        "raw_names": result.raw_names,
                        # **どの月の観測かを payload にも書く。** パスだけに
                        # 頼ると、ディレクトリを動かしたときに月が分からなくなる
                        "month": self.month,
                    }
                ),
                encoding="utf-8",
            )
        return result


    def prefetch(
        self,
        domains: Iterable[str],
        *,
        on_result: Callable[[str, CtResult], None] | None = None,
    ) -> dict[str, CtResult]:
        """まとめて取る。**応答待ちだけを重ねる。**

        投げる間隔は `_throttle()` が全スレッドで共有しているので、crt.sh
        から見た単位時間あたりの本数は直列のときと変わらない。

        戻り値は入力順の dict。**完了順にしない。** 完了順は実行ごとに
        変わるので、そのまま集計に流すと同じ入力から違うバイト列が出て
        原則6（冪等）が壊れる。

        `on_result` は**呼び出し側のスレッドで**、入力順に呼ぶ。進捗表示は
        スレッド安全に作っていないので、worker から呼ばせない。
        """
        ordered: list[str] = []
        seen: set[str] = set()
        for d in domains:
            # 同じ起点ドメインを持つ企業が複数あることがある。
            # **2度取らない**（相手への本数がそのぶん増える）
            if d and d not in seen:
                seen.add(d)
                ordered.append(d)
        if not ordered:
            return {}

        out: dict[str, CtResult] = {}
        own = self._client is None
        client = self._client or httpx.Client(timeout=self.timeout, follow_redirects=True)
        try:
            with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
                # map は入力順に結果を返す。完了順ではない
                for domain, result in zip(
                    ordered,
                    pool.map(lambda d: self.search(d, client=client), ordered),
                    strict=True,
                ):
                    out[domain] = result
                    if on_result is not None:
                        on_result(domain, result)
        finally:
            if own:
                client.close()
        return out


def _extract(domain: str, rows: list[dict]) -> CtResult:
    """crt.sh の行から eTLD+1 を取り出して重複排除する。"""
    seen: dict[str, None] = {}
    raw = 0
    for row in rows or []:
        names = str(row.get("name_value") or "")
        for fqdn in names.splitlines():
            candidate = fqdn.strip().lstrip("*.").rstrip(".").lower()
            if not candidate or " " in candidate:
                continue
            raw += 1
            apex = etld_plus_one(candidate)
            if apex:
                seen.setdefault(apex, None)
    return CtResult(domain=domain, found=sorted(seen), raw_names=raw)


class StaticCtSource:
    """テスト用。ネットワークに出ない。"""

    def __init__(self, mapping: dict[str, list[str]] | None = None) -> None:
        self.mapping = mapping or {}
        self.calls: list[str] = []

    def search(self, domain: str) -> CtResult:
        self.calls.append(domain)
        found = self.mapping.get(domain, [])
        return CtResult(domain=domain, found=list(found), raw_names=len(found))

    def prefetch(
        self,
        domains: Iterable[str],
        *,
        on_result: Callable[[str, CtResult], None] | None = None,
    ) -> dict[str, CtResult]:
        return _serial_prefetch(self.search, domains, on_result)


class DisabledCtSource:
    """CT 探索を無効にしたときの実装。呼ばれたら空を返す。"""

    def search(self, domain: str) -> CtResult:
        return CtResult(domain=domain, found=[], raw_names=0)

    def prefetch(
        self,
        domains: Iterable[str],
        *,
        on_result: Callable[[str, CtResult], None] | None = None,
    ) -> dict[str, CtResult]:
        return _serial_prefetch(self.search, domains, on_result)


def _serial_prefetch(
    search: Callable[[str], CtResult],
    domains: Iterable[str],
    on_result: Callable[[str, CtResult], None] | None,
) -> dict[str, CtResult]:
    """ネットワークに出ない実装用。**並行にする意味が無いので直列。**"""
    out: dict[str, CtResult] = {}
    for domain in domains:
        if not domain or domain in out:
            continue
        out[domain] = search(domain)
        if on_result is not None:
            on_result(domain, out[domain])
    return out
