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
"""

from __future__ import annotations

import json
import time
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


class CtSource(Protocol):
    def search(self, domain: str) -> CtResult: ...


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
        client: httpx.Client | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        #: キャッシュを区切る月（`YYYY-MM`）。**月次計測では必ず渡す。**
        #: 渡さないと先月の応答を今月の観測として使ってしまう
        self.month = month
        self.min_interval = 1.0 / qps if qps > 0 else 0.0
        self.timeout = timeout
        self.retries = retries
        self._client = client
        self._last_call = 0.0

    def _throttle(self) -> None:
        if self.min_interval <= 0:
            return
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
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

    def search(self, domain: str) -> CtResult:
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

        client = self._client or httpx.Client(timeout=self.timeout, follow_redirects=True)
        close = self._client is None
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


class DisabledCtSource:
    """CT 探索を無効にしたときの実装。呼ばれたら空を返す。"""

    def search(self, domain: str) -> CtResult:
        return CtResult(domain=domain, found=[], raw_names=0)
