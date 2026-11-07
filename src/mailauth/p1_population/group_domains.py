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
GROUP_FIELDS = ("edinet_code", "subsidiary", "corporate_number", "domain", "status", "query")
#: 引き方の版。上がったら、found・no_url 以外の名前を引き直す（2：exist_flg をやめ、件数を
#: 増やし、法人の種類で絞り、閉鎖した法人を除く。3：同じ商号が複数あれば、有報の表の
#: 住所の都道府県で絞る。2026-11-08）
QUERY_VERSION = 3
CACHE_VERSION = QUERY_VERSION
#: 1回の検索で受け取る件数。部分一致なので、ありふれた商号（「日本道路株式会社」）は
#: 50 件では完全一致の法人が入らないことがあった
SEARCH_LIMIT = 1000
#: 商号の法人格 → gBizINFO の corporate_type（301 株式会社・302 有限会社・305 合同会社）
_CORPORATE_TYPE = (("株式会社", "301"), ("有限会社", "302"), ("合同会社", "305"))


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

    def _cache(self, name: str, prefecture: str | None = None) -> Path | None:
        if self.cache_dir is None:
            return None
        key = hashlib.sha256(f"{_norm(name)}|{prefecture or ''}".encode()).hexdigest()[:24]
        return self.cache_dir / "names" / f"{key}.json"

    def lookup(
        self, name: str, prefecture: str | None = None
    ) -> tuple[str | None, str | None, str]:
        """(法人番号, ドメイン, 状態)。状態は found / no_url / ambiguous / not_found / error。

        同じ商号の法人が複数あれば、`prefecture`（有報の表の住所）で絞る。
        """
        path = self._cache(name, prefecture)
        if path is not None and path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("version") == CACHE_VERSION:
                    return data["number"], data["domain"], data["status"]
            except (OSError, ValueError, KeyError):
                pass
        result = self._lookup(name, prefecture)
        if path is not None and result[2] != "error":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"version": CACHE_VERSION, "number": result[0],
                                        "domain": result[1], "status": result[2]}),
                            encoding="utf-8")
        return result

    def _lookup(
        self, name: str, prefecture: str | None = None
    ) -> tuple[str | None, str | None, str]:
        # **`exist_flg` は付けない。** 「閉鎖していない」ではなく「法人活動情報（届出・
        # 補助金・調達など）がある」に絞るパラメータで（API 仕様、2026-11-08 に確認）、
        # 子会社の多くがこれで落ちて not_found になっていた（3,000 件中 1,635 件）
        params = {"name": name, "limit": str(SEARCH_LIMIT)}
        ctype = next((code for form, code in _CORPORATE_TYPE if form in name), None)
        if ctype:
            params["corporate_type"] = ctype
        payload = self._get(GBIZINFO_ENDPOINT, params)
        if payload is None:
            return None, None, "error"
        # 閉鎖した法人（登記記録の閉鎖）は採らない
        hits = [h for h in (payload.get("hojin-infos") or [])
                if _norm(h.get("name")) == _norm(name) and not h.get("close_date")]
        if not hits:
            return None, None, "not_found"
        if len({h.get("corporate_number") for h in hits}) > 1 and prefecture:
            # **同じ商号は、有報の表に書かれた住所の都道府県で絞る。** 1つに決まる
            # ときだけ採る
            hits = [h for h in hits if str(h.get("location") or "").startswith(prefecture)]
            if not hits:
                return None, None, "ambiguous"
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


def csv_query_version(path: str | Path) -> int | None:
    """一覧を書いたときの引き方の版。列が無い（古い一覧）なら None、一覧が無ければ今の版。"""
    p = Path(path)
    if not p.exists():
        return QUERY_VERSION
    with p.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            try:
                return int(row.get("query") or "")
            except ValueError:
                return None
    return QUERY_VERSION


def write_csv(path: str | Path, data: dict[tuple[str, str], tuple[str, str, str]]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(GROUP_FIELDS)
        for (code, name) in sorted(data):
            number, domain, status = data[(code, name)]
            w.writerow([code, name, number, domain, status, QUERY_VERSION])


def scan(
    search: GbizNameSearch,
    subsidiaries: dict[str, tuple[str, list[str], str]],
    existing: dict[tuple[str, str], tuple[str, str, str]],
    *,
    max_lookups: int,
    queried_with: int | None = QUERY_VERSION,
    places: dict[tuple[str, str], str] | None = None,
) -> tuple[dict[tuple[str, str], tuple[str, str, str]], dict[str, int]]:
    """子会社名を順に引く。**引き終えた名前（error 以外）は引き直さない。**

    子会社名の一覧から消えた名前（読み方を直して落ちた地の文など）は残さない。
    """
    current = {(code, name) for code, (_d, names, _o) in subsidiaries.items() for name in names}
    out = {k: v for k, v in existing.items() if k in current}
    stats = {"names": 0, "already": 0, "looked_up": 0, "deferred": 0,
             "found": 0, "no_url": 0, "ambiguous": 0, "not_found": 0, "error": 0}
    pairs: Iterable[tuple[str, str]] = (
        (code, name) for code, (_doc, names, _on) in sorted(subsidiaries.items()) for name in names
    )
    for code, name in pairs:
        stats["names"] += 1
        prev = existing.get((code, name))
        # 引き方が古ければ、found 以外は引き直す（見つからなかった理由が引き方かもしれない）
        # no_url は法人が1つに決まっている（引き方を変えても結果は変わらない）
        stale = (queried_with != QUERY_VERSION and bool(prev)
                 and prev[2] not in ("found", "no_url"))
        if prev and prev[2] and prev[2] != "error" and not stale:
            stats["already"] += 1
            continue
        if stale and stats["looked_up"] >= max_lookups:
            # 古い結果は今の版の一覧に残さない（残すと次に引き直す対象から外れる）
            out.pop((code, name), None)
            stats["deferred"] += 1
            continue
        if stats["looked_up"] >= max_lookups:
            stats["deferred"] += 1
            continue
        number, domain, status = search.lookup(name, (places or {}).get((code, name)))
        stats["looked_up"] += 1
        stats[status] += 1
        out[(code, name)] = (number or "", domain or "", status)
    return out, stats
