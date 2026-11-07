"""子会社名から、その会社の企業サイトのドメインを引く（グループ会社、BACKLOG 52）。

有報の「関係会社の状況」から読んだ子会社名（`configs/domains/subsidiaries.csv`）を、
gBizINFO の法人検索（名前の部分一致）で引き、**商号が完全に一致する法人が1つだけ**
のときに、その法人番号の企業ホームページ（`company_url`）のドメインを採る。

同じ商号の法人が複数あれば採らない（別の会社を測らない）。上場会社の数字には
混ぜず、`configs/domains/subsidiary_domains.csv` に置くだけ。gBizINFO は
政府標準利用規約に沿って使う（出典は母集団設定の attribution に記載）。
"""

from __future__ import annotations

import csv
import hashlib
import json
import time
import unicodedata
from collections.abc import Iterable
from pathlib import Path

import httpx

from ..normalize import domain_from_url
from .enrich import GBIZINFO_ENDPOINT, USER_AGENT

GROUP_CSV = "configs/domains/subsidiary_domains.csv"
GROUP_FIELDS = ("edinet_code", "subsidiary", "corporate_number", "domain", "status")
CACHE_VERSION = 1


def _norm(name: str | None) -> str:
    return unicodedata.normalize("NFKC", name or "").replace(" ", "").replace("　", "")


class GbizNameSearch:
    """gBizINFO の法人検索。キャッシュは名前ごと。"""

    def __init__(
        self,
        token: str,
        *,
        qps: float = 5.0,
        timeout: float = 20.0,
        cache_dir: Path | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.token = token
        self.min_interval = 1.0 / qps if qps > 0 else 0.0
        self.cache_dir = cache_dir
        self._client = client or httpx.Client(timeout=timeout)
        self._last = 0.0
        self.calls = 0

    def _get(self, url: str, params: dict[str, str] | None = None) -> dict | None:
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        self.calls += 1
        try:
            resp = self._client.get(url, params=params, headers={
                "X-hojinInfo-api-token": self.token,
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            })
        except httpx.HTTPError:
            return None
        if resp.status_code == 404:
            return {}
        if resp.status_code != 200:
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    def _cache(self, name: str) -> Path | None:
        if self.cache_dir is None:
            return None
        key = hashlib.sha256(_norm(name).encode()).hexdigest()[:24]
        return self.cache_dir / "names" / f"{key}.json"

    def lookup(self, name: str) -> tuple[str | None, str | None, str]:
        """(法人番号, ドメイン, 状態)。状態は found / no_url / ambiguous / not_found / error。"""
        path = self._cache(name)
        if path is not None and path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("version") == CACHE_VERSION:
                    return data["number"], data["domain"], data["status"]
            except (OSError, ValueError, KeyError):
                pass
        result = self._lookup(name)
        if path is not None and result[2] != "error":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"version": CACHE_VERSION, "number": result[0],
                                        "domain": result[1], "status": result[2]}),
                            encoding="utf-8")
        return result

    def _lookup(self, name: str) -> tuple[str | None, str | None, str]:
        payload = self._get(GBIZINFO_ENDPOINT, {"name": name, "exist_flg": "true", "limit": "50"})
        if payload is None:
            return None, None, "error"
        hits = [h for h in (payload.get("hojin-infos") or [])
                if _norm(h.get("name")) == _norm(name)]
        if not hits:
            return None, None, "not_found"
        if len({h.get("corporate_number") for h in hits}) > 1:
            return None, None, "ambiguous"
        hit = hits[0]
        number = str(hit.get("corporate_number") or "") or None
        url = hit.get("company_url")
        if not url and number:
            detail = self._get(f"{GBIZINFO_ENDPOINT}/{number}")
            if detail is None:
                return number, None, "error"
            info = detail.get("hojin-infos") or []
            url = info[0].get("company_url") if info else None
        domain = domain_from_url(url) if url else None
        return number, domain, "found" if domain else "no_url"


def read_csv(path: str | Path) -> dict[tuple[str, str], tuple[str, str, str]]:
    p = Path(path)
    if not p.exists():
        return {}
    out: dict[tuple[str, str], tuple[str, str, str]] = {}
    with p.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            out[(row["edinet_code"], row["subsidiary"])] = (
                row.get("corporate_number") or "", row.get("domain") or "", row.get("status") or "",
            )
    return out


def write_csv(path: str | Path, data: dict[tuple[str, str], tuple[str, str, str]]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(GROUP_FIELDS)
        for (code, name) in sorted(data):
            number, domain, status = data[(code, name)]
            w.writerow([code, name, number, domain, status])


def scan(
    search: GbizNameSearch,
    subsidiaries: dict[str, tuple[str, list[str], str]],
    existing: dict[tuple[str, str], tuple[str, str, str]],
    *,
    max_lookups: int,
) -> tuple[dict[tuple[str, str], tuple[str, str, str]], dict[str, int]]:
    """子会社名を順に引く。**引き終えた名前（error 以外）は引き直さない。**"""
    out = dict(existing)
    stats = {"names": 0, "already": 0, "looked_up": 0, "deferred": 0,
             "found": 0, "no_url": 0, "ambiguous": 0, "not_found": 0, "error": 0}
    pairs: Iterable[tuple[str, str]] = (
        (code, name) for code, (_doc, names, _on) in sorted(subsidiaries.items()) for name in names
    )
    for code, name in pairs:
        stats["names"] += 1
        prev = existing.get((code, name))
        if prev and prev[2] and prev[2] != "error":
            stats["already"] += 1
            continue
        if stats["looked_up"] >= max_lookups:
            stats["deferred"] += 1
            continue
        number, domain, status = search.lookup(name)
        stats["looked_up"] += 1
        stats[status] += 1
        out[(code, name)] = (number or "", domain or "", status)
    return out, stats
