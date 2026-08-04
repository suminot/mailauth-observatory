"""SEC EDGAR からの identity 補完（DESIGN.md P1 / Sprint 1.5）。

**SEC は User-Agent を必須とし、10 req/s 未満を求めている。** 守らないと
IP 単位で遮断される。ここは公開データ（パブリックドメイン）だが、
アクセスの作法は規約である。

取れるもの
  - `company_tickers.json` ── ティッカー -> CIK。1ファイルで全件
  - `submissions/CIK##########.json` ── 公式サイトと SIC

**SEC EDGAR は米国提出者しかカバーしない。** Global 500 は米国以外が
7割を占めるので、これを identity の主キーにはできない。主キーは LEI。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from ..config import credential
from ..paths import cache_root

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

#: SEC は 10 req/s 未満を要求している。余裕を持って 8 に絞る
MAX_QPS = 8.0
TIMEOUT_SEC = 30.0


class SecEdgarError(RuntimeError):
    pass


def user_agent() -> str:
    """SEC は連絡先つきの User-Agent を必須としている。

    `.env` の MAILAUTH_CONTACT_EMAIL を使う。**未設定なら止める。**
    偽の連絡先を送るのは規約違反であり、黙って続けるべきではない。
    """
    contact = credential("MAILAUTH_CONTACT_EMAIL")
    if not contact:
        raise SecEdgarError(
            "MAILAUTH_CONTACT_EMAIL が未設定です。SEC EDGAR は連絡先を含む "
            "User-Agent を必須としており、偽の値を送るのは規約違反です。"
            ".env に設定してください"
        )
    return f"mailauth-observatory/0.1 ({contact})"


@dataclass
class SecFacts:
    cik: str | None = None
    website: str | None = None
    sic: str | None = None
    sic_label: str | None = None
    name: str | None = None


@dataclass
class SecStats:
    ticker_entries: int = 0
    submissions_fetched: int = 0
    submissions_failed: int = 0
    cik_from_ticker: int = 0
    website_found: int = 0
    sic_found: int = 0
    notes: list[str] = field(default_factory=list)


class _RateLimiter:
    def __init__(self, qps: float) -> None:
        self._interval = 1.0 / qps if qps > 0 else 0.0
        self._last = 0.0

    def wait(self) -> None:
        if self._interval <= 0:
            return
        elapsed = time.monotonic() - self._last
        if elapsed < self._interval:
            time.sleep(self._interval - elapsed)
        self._last = time.monotonic()


class SecEdgarClient:
    """CIK 解決と submissions の取得。

    `company_tickers.json` はキャッシュする。全件で数 MB あり、
    1社ずつ引き直す理由がない。
    """

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        qps: float = MAX_QPS,
        cache_dir: Path | None = None,
    ) -> None:
        self._client = client
        self._owned = client is None
        self._limiter = _RateLimiter(qps)
        self.cache_dir = cache_dir or (cache_root() / "sec_edgar")
        self._tickers: dict[str, int] | None = None
        self.stats = SecStats()

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=TIMEOUT_SEC,
                follow_redirects=True,
                headers={"User-Agent": user_agent(), "Accept-Encoding": "gzip, deflate"},
            )
        return self._client

    def close(self) -> None:
        if self._owned and self._client is not None:
            self._client.close()
            self._client = None

    def get_json(self, url: str) -> Any:
        self._limiter.wait()
        resp = self._http().get(url)
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise SecEdgarError(f"SEC が {resp.status_code} を返した: {url}")
        return resp.json()

    # -- ティッカー -> CIK ---------------------------------------------------

    def load_tickers(self, *, refresh: bool = False) -> dict[str, int]:
        cache = self.cache_dir / "company_tickers.json"
        if not refresh and cache.is_file():
            payload = json.loads(cache.read_text(encoding="utf-8"))
        else:
            payload = self.get_json(TICKERS_URL)
            if payload is None:
                raise SecEdgarError("company_tickers.json を取得できなかった")
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )

        out: dict[str, int] = {}
        for entry in (payload or {}).values():
            ticker = str(entry.get("ticker") or "").strip().upper()
            cik = entry.get("cik_str")
            if ticker and cik is not None:
                out[ticker] = int(cik)
        self._tickers = out
        self.stats.ticker_entries = len(out)
        return out

    def cik_for_ticker(self, ticker: str | None) -> str | None:
        if not ticker:
            return None
        if self._tickers is None:
            self.load_tickers()
        cik = (self._tickers or {}).get(ticker.strip().upper())
        if cik is None:
            return None
        self.stats.cik_from_ticker += 1
        return f"{cik:010d}"

    # -- submissions ---------------------------------------------------------

    def facts_for_cik(self, cik: str | None) -> SecFacts:
        """公式サイトと SIC を取る。取れなくても例外にしない。

        1社の取得失敗で母集団全体を止めない。件数は manifest に出る（原則4）。
        """
        facts = SecFacts(cik=cik)
        if not cik:
            return facts
        try:
            payload = self.get_json(SUBMISSIONS_URL.format(cik=int(cik)))
        except (SecEdgarError, httpx.HTTPError, ValueError) as exc:
            self.stats.submissions_failed += 1
            if len(self.stats.notes) < 10:
                self.stats.notes.append(f"CIK {cik}: {exc}")
            return facts
        if payload is None:
            self.stats.submissions_failed += 1
            return facts

        self.stats.submissions_fetched += 1
        facts.name = (payload.get("name") or "").strip() or None
        website = (payload.get("website") or "").strip()
        if website:
            facts.website = website
            self.stats.website_found += 1
        sic = str(payload.get("sic") or "").strip()
        if sic:
            facts.sic = sic
            facts.sic_label = (payload.get("sicDescription") or "").strip() or None
            self.stats.sic_found += 1
        return facts


EXCHANGE_URL = "https://www.sec.gov/files/company_tickers_exchange.json"

#: 計測対象にする取引所。OTC は上場企業とは言えないので既定では外す。
#: 「上場」の定義を暗黙にしないため、設定から渡せるようにしてある
DEFAULT_EXCHANGES = ("NYSE", "Nasdaq", "CBOE")


@dataclass
class ListedRow:
    """SEC の取引所リストの1行。EDINET の行と同じ役割。"""

    cik: str
    name: str
    ticker: str | None = None
    exchange: str | None = None
    website: str | None = None
    sic: str | None = None
    sic_label: str | None = None


def load_exchange_listing(
    client: SecEdgarClient,
    *,
    exchanges: tuple[str, ...] | list[str] = DEFAULT_EXCHANGES,
    refresh: bool = False,
) -> tuple[list[ListedRow], dict[str, Any]]:
    """`company_tickers_exchange.json` から米国上場企業を読む。

    **これは Fortune 500 ではない。** Fortune の順位は編集著作物であり、
    CC0 / CC BY-SA のソースからは500社規模で再構築できない
    （Wikidata に所属情報が無く、Wikipedia の該当記事は上位100社まで）。
    国内側と同じく「全上場を測り、絞り込みはビューで行う」形にしてある。
    """
    cache = client.cache_dir / "company_tickers_exchange.json"
    if not refresh and cache.is_file():
        payload = json.loads(cache.read_text(encoding="utf-8"))
    else:
        payload = client.get_json(EXCHANGE_URL)
        if payload is None:
            raise SecEdgarError("company_tickers_exchange.json を取得できなかった")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    fields = [str(f) for f in (payload.get("fields") or [])]
    try:
        idx = {name: fields.index(name) for name in ("cik", "name", "ticker", "exchange")}
    except ValueError as exc:
        raise SecEdgarError(f"想定と違う列構成: {fields}") from exc

    allowed = {e.lower() for e in exchanges}
    rows: list[ListedRow] = []
    stats = {
        "total": 0,
        "kept": 0,
        "by_exchange": {},
        "no_exchange": 0,
        "duplicate_cik": 0,
    }
    seen: set[str] = set()

    for raw in payload.get("data") or []:
        stats["total"] += 1
        exchange = raw[idx["exchange"]]
        label = str(exchange) if exchange else None
        key = label or "(none)"
        stats["by_exchange"][key] = stats["by_exchange"].get(key, 0) + 1
        if label is None:
            stats["no_exchange"] += 1
            continue
        if label.lower() not in allowed:
            continue

        cik = f"{int(raw[idx['cik']]):010d}"
        if cik in seen:
            # 1社が複数クラスの株式を出していると CIK が重複する
            stats["duplicate_cik"] += 1
            continue
        seen.add(cik)
        rows.append(
            ListedRow(
                cik=cik,
                name=str(raw[idx["name"]]).strip(),
                ticker=(str(raw[idx["ticker"]]).strip() or None),
                exchange=label,
            )
        )

    stats["kept"] = len(rows)
    stats["by_exchange"] = dict(sorted(stats["by_exchange"].items()))
    return sorted(rows, key=lambda r: r.cik), stats
