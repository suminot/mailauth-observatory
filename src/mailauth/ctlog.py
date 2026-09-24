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

`concurrency` 本を超えては走らせない。多くすれば速くなるものではなく、
**qps がそのまま下限**になる。実物で測った（2026-09-24）:

  直列     1件 9.1秒
  同時4本  1件 2.4秒
  同時8本  1件 2.3秒   ← 4本で既に下限（1件2.0秒）に届いている

**8本にしても縮まない。** 既に qps で律速しているため。
"""

from __future__ import annotations

import json
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
        try:
            while True:
                self._throttle()
                try:
                    resp = client.get(
                        CRTSH_ENDPOINT,
                        params={"q": f"%.{domain}", "output": "json"},
                        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                    )
                except httpx.HTTPError as exc:
                    if attempt < self.retries:
                        attempt += 1
                        time.sleep(2 ** attempt)
                        continue
                    return CtResult(domain=domain, error=f"request failed: {exc}"[:200])

                if resp.status_code in (429, 502, 503, 504) and attempt < self.retries:
                    attempt += 1
                    time.sleep(2 ** attempt)
                    continue
                if resp.status_code != 200:
                    return CtResult(domain=domain, error=f"HTTP {resp.status_code}")
                try:
                    rows = resp.json()
                except ValueError:
                    return CtResult(domain=domain, error="JSON として解釈できない応答")
                break
        finally:
            if close:
                client.close()

        result = _extract(domain, rows)
        result.cache_month = self.month
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
