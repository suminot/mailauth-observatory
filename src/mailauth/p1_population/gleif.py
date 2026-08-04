"""GLEIF から LEI を引く（DESIGN.md P1 / Sprint 1.5）。

**Global 500 の identity 主キーは LEI である。** SEC EDGAR は米国提出者しか
カバーせず、法人番号は日本、EDINET コードも日本にしかない。全世界を1つの
キーで揃えられるのは LEI だけである。

ただし LEI は全社にあるわけではない。**欠損は前提として設計する。**
欠損時は cik / houjin_bangou / 正規化した商号の順にフォールバックし、
どのキーで同定したかを必ず記録する。後から「なぜこの2社が同一と判定されたか」
を追えないと、誤マージに気付けない。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

API_URL = "https://api.gleif.org/api/v1/lei-records"
TIMEOUT_SEC = 30.0
#: GLEIF の公表レート制限。余裕を見て絞る
MAX_QPS = 5.0


class GleifError(RuntimeError):
    pass


@dataclass
class LeiRecord:
    lei: str
    legal_name: str | None = None
    country: str | None = None
    status: str | None = None


@dataclass
class GleifStats:
    looked_up: int = 0
    found: int = 0
    failed: int = 0
    ambiguous: int = 0
    notes: list[str] = field(default_factory=list)


class GleifClient:
    def __init__(
        self, *, client: httpx.Client | None = None, qps: float = MAX_QPS
    ) -> None:
        self._client = client
        self._owned = client is None
        self._interval = 1.0 / qps if qps > 0 else 0.0
        self._last = 0.0
        self.stats = GleifStats()

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=TIMEOUT_SEC,
                follow_redirects=True,
                headers={"Accept": "application/vnd.api+json"},
            )
        return self._client

    def close(self) -> None:
        if self._owned and self._client is not None:
            self._client.close()
            self._client = None

    def _wait(self) -> None:
        if self._interval <= 0:
            return
        elapsed = time.monotonic() - self._last
        if elapsed < self._interval:
            time.sleep(self._interval - elapsed)
        self._last = time.monotonic()

    def _get(self, params: dict[str, Any]) -> list[dict]:
        self._wait()
        try:
            resp = self._http().get(API_URL, params=params)
        except httpx.HTTPError as exc:
            raise GleifError(f"GLEIF に接続できない: {exc}") from exc
        if resp.status_code != 200:
            raise GleifError(f"GLEIF が {resp.status_code} を返した")
        return list((resp.json() or {}).get("data") or [])

    @staticmethod
    def _record(item: dict) -> LeiRecord:
        attrs = item.get("attributes") or {}
        entity = attrs.get("entity") or {}
        legal = entity.get("legalName") or {}
        address = entity.get("legalAddress") or {}
        return LeiRecord(
            lei=str(attrs.get("lei") or item.get("id") or ""),
            legal_name=(legal.get("name") or "").strip() or None,
            country=(address.get("country") or "").strip() or None,
            status=(entity.get("status") or "").strip() or None,
        )

    def by_lei(self, lei: str) -> LeiRecord | None:
        """LEI そのもので引く。Wikidata の値の裏取りに使う。"""
        self.stats.looked_up += 1
        try:
            data = self._get({"filter[lei]": lei, "page[size]": 1})
        except GleifError as exc:
            self.stats.failed += 1
            if len(self.stats.notes) < 10:
                self.stats.notes.append(f"{lei}: {exc}")
            return None
        if not data:
            return None
        self.stats.found += 1
        return self._record(data[0])

    def by_name(self, name: str, *, country: str | None = None) -> LeiRecord | None:
        """商号で引く。**一意に定まらなければ None を返す。**

        「たぶんこれだろう」で LEI を付けると、別会社を同一視した集計が
        できあがる。曖昧なものは曖昧なまま残し、件数を manifest に出す。
        """
        self.stats.looked_up += 1
        params: dict[str, Any] = {
            "filter[entity.legalName]": name,
            "page[size]": 5,
        }
        if country:
            params["filter[entity.legalAddress.country]"] = country
        try:
            data = self._get(params)
        except GleifError as exc:
            self.stats.failed += 1
            if len(self.stats.notes) < 10:
                self.stats.notes.append(f"{name}: {exc}")
            return None

        active = [
            item
            for item in data
            if str(((item.get("attributes") or {}).get("entity") or {}).get("status"))
            .upper()
            != "INACTIVE"
        ]
        candidates = active or data
        if not candidates:
            return None
        if len(candidates) > 1:
            self.stats.ambiguous += 1
            if len(self.stats.notes) < 10:
                self.stats.notes.append(
                    f"{name}: 候補が {len(candidates)} 件あり一意に定まらない"
                )
            return None
        self.stats.found += 1
        return self._record(candidates[0])
