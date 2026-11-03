"""日本語版ウィキペディアの「基礎情報 会社」から公式サイトを取る（BACKLOG 28）。

国内で gBizINFO・Wikidata・.co.jp の推測のあとも公式サイトが無い会社
（2026-11 で 356 社）に使う。Wikidata に公式サイト（P856）が無くても、
日本語版の記事の基礎情報には「外部リンク」が書いてあることがある。

**同じ会社の記事であることは、基礎情報の「市場情報」に書かれた証券コード
で確かめる。** 名前だけで当てない ── 同名の別会社や、別の意味の記事がある。
コードは基礎情報の**市場情報の欄だけ**で探す（資本金や従業員数の数字に
4桁が紛れ込むため）。

2026-11-03 の probe（`probe-wikipedia.yml`、run 41650240337）で、356 社中
69 社が取れた（記事が無い 248、基礎情報の無い記事 25、外部リンクが無い 11、
コードが合わない 3）。

守っていること
  - 読むのは MediaWiki の API（action=query）だけ。1回に 50 記事まで、
    連絡先つきの User-Agent で、1秒に1本まで
  - 取れたドメインが**既に他社に付いていれば採らない**（親子上場で親の
    ドメインが子の記事に書かれていることがある）
  - SNS や EC モールなど、会社のものでないドメインは採らない
  - **引けなかったことと、書いていなかったことを分ける**（原則5）
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from ..normalize import domain_from_url

API_URL = "https://ja.wikipedia.org/w/api.php"
#: 1回に引く記事の数（MediaWiki の上限）
BATCH = 50
#: 投げる間隔（秒）
INTERVAL_SEC = 1.0
RETRY_WAITS = (5.0, 20.0)

_LEGAL_FORM = re.compile(r"^(株式会社|\(株\)|（株）)\s*|\s*(株式会社|\(株\)|（株）)$")
_INFOBOX = re.compile(r"\{\{\s*基礎情報[ _]会社")
_ANY_INFOBOX = re.compile(r"\{\{\s*((?:基礎情報|Infobox)[ _][^|}\n]{1,30})")
_URL = re.compile(r"https?://[^\s\]\}|<>\"']+")
_URL_TEMPLATE = re.compile(r"\{\{\s*URL\s*\|\s*([^|}\s]+)", re.IGNORECASE)

#: 会社のものでないドメイン。基礎情報の外部リンクに SNS や EC の店舗を
#: 書く記事がある
NOT_COMPANY = frozenset(
    {
        "facebook.com", "twitter.com", "x.com", "instagram.com", "youtube.com",
        "linkedin.com", "note.com", "ameblo.jp", "rakuten.co.jp", "amazon.co.jp",
        "wikipedia.org", "wikimedia.org", "jpx.co.jp", "archive.org", "line.me",
        "tiktok.com", "google.com", "yahoo.co.jp",
    }
)


def candidate_titles(name: str) -> list[str]:
    """記事の名前の候補。法人格を落としたものと、「 (企業)」を付けたもの。

    EDINET の社名は英数字が全角のことがある（「Ｕｍｉｏｓ株式会社」）。記事の
    名前は半角なので、NFKC で揃えたものも試す。
    """
    out: list[str] = []
    for form in (name or "", unicodedata.normalize("NFKC", name or "")):
        base = _LEGAL_FORM.sub("", form.strip()).strip()
        for title in (base, f"{base} (企業)"):
            if base and title not in out:
                out.append(title)
    return out


def _infobox(wikitext: str) -> str | None:
    """「基礎情報 会社」の中身（入れ子の {{ }} を数えて閉じるところまで）。"""
    m = _INFOBOX.search(wikitext or "")
    if not m:
        return None
    depth = 0
    i = m.start()
    while i < len(wikitext):
        if wikitext.startswith("{{", i):
            depth += 1
            i += 2
            continue
        if wikitext.startswith("}}", i):
            depth -= 1
            i += 2
            if depth == 0:
                return wikitext[m.end() : i - 2]
            continue
        i += 1
    return wikitext[m.end() :]


def _param(infobox: str, key: str) -> str | None:
    """基礎情報の1つの欄の値。入れ子の {{ }} と [[ ]] の中の | では切らない。"""
    m = re.search(rf"\|\s*{re.escape(key)}\s*=", infobox)
    if not m:
        return None
    i = j = m.end()
    depth = 0
    while j < len(infobox):
        if infobox.startswith("{{", j) or infobox.startswith("[[", j):
            depth += 1
            j += 2
            continue
        if infobox.startswith("}}", j) or infobox.startswith("]]", j):
            depth = max(depth - 1, 0)
            j += 2
            continue
        if infobox[j] == "|" and depth == 0:
            break
        j += 1
    return infobox[i:j].strip()


def code_listed(infobox: str, code: str) -> bool:
    """市場情報の欄に、その会社の証券コード（4桁）が書いてあるか。"""
    market = _param(infobox, "市場情報")
    if not market or not code:
        return False
    return re.search(rf"(?<![0-9A-Za-z]){re.escape(code)}(?![0-9A-Za-z])", market) is not None


def external_link(infobox: str) -> str | None:
    """「外部リンク」の欄の最初の URL。"""
    value = _param(infobox, "外部リンク")
    if not value:
        return None
    m = _URL.search(value)
    if m:
        return m.group(0).rstrip(".,")
    t = _URL_TEMPLATE.search(value)
    if t:
        return "https://" + t.group(1).split("://", 1)[-1]
    return None


@dataclass
class JawikiStats:
    attempted: int = 0
    no_article: int = 0
    no_company_infobox: int = 0
    code_mismatch: int = 0
    no_external_link: int = 0
    not_company_domain: int = 0
    found: int = 0
    taken_by_other: int = 0
    #: 引けなかった（API が落ちていた）。**「記事が無い」とは数えない**
    fetch_failed: int = 0
    requests: int = 0
    samples: list[str] = field(default_factory=list)
    #: 会社の基礎情報が無い記事が使っていた基礎情報の名前と数
    other_infoboxes: Counter[str] = field(default_factory=Counter)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "no_article": self.no_article,
            "no_company_infobox": self.no_company_infobox,
            "code_mismatch": self.code_mismatch,
            "no_external_link": self.no_external_link,
            "not_company_domain": self.not_company_domain,
            "found": self.found,
            "taken_by_other": self.taken_by_other,
            "fetch_failed": self.fetch_failed,
            "requests": self.requests,
            "sample_found": self.samples[:10],
            "other_infoboxes": dict(self.other_infoboxes.most_common(10)),
        }


def live_fetch() -> Callable[[list[str]], dict[str, str | None]]:
    """記事名 → 本文（無ければ None）。リダイレクトはたどる。

    User-Agent は Wikidata と同じ（連絡先 `MAILAUTH_CONTACT_EMAIL` を入れる）。
    """
    import httpx

    from .wikidata import user_agent

    client = httpx.Client(headers={"User-Agent": user_agent()}, timeout=30)
    last = [0.0]

    def fetch(titles: list[str]) -> dict[str, str | None]:
        params = {
            "action": "query", "format": "json", "formatversion": "2",
            "prop": "revisions", "rvprop": "content", "rvslots": "main",
            "redirects": "1", "titles": "|".join(titles),
        }
        for wait in (*RETRY_WAITS, None):
            pause = INTERVAL_SEC - (time.monotonic() - last[0])
            if pause > 0:
                time.sleep(pause)
            last[0] = time.monotonic()
            try:
                resp = client.get(API_URL, params=params)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise httpx.HTTPStatusError("retry", request=resp.request, response=resp)
                resp.raise_for_status()
                query = resp.json().get("query", {})
                break
            except (httpx.TransportError, httpx.HTTPStatusError, ValueError):
                if wait is None:
                    raise
                time.sleep(wait)
        alias: dict[str, str] = {}
        for key in ("normalized", "redirects"):
            for n in query.get(key, []):
                alias[n["from"]] = n["to"]
        got: dict[str, str | None] = {}
        for page in query.get("pages", []):
            revs = page.get("revisions") or []
            got[page["title"]] = revs[0]["slots"]["main"]["content"] if revs else None
        out: dict[str, str | None] = {}
        for t in titles:
            final, seen = t, set()
            while final in alias and final not in seen:
                seen.add(final)
                final = alias[final]
            out[t] = got.get(final)
        return out

    return fetch


class JawikiWebsites:
    """起点の無い会社に、日本語版の記事の基礎情報から公式サイトを付ける。

    `fetch` を渡せるのは検査のため（ネットワークに出ない）。
    """

    def __init__(self, fetch: Callable[[list[str]], dict[str, str | None]]) -> None:
        self.fetch = fetch
        self.stats = JawikiStats()

    def websites_for(
        self, companies: Iterable[tuple[str, str, str | None]], taken: set[str]
    ) -> dict[str, str]:
        """(鍵, 社名, 証券コード) → {鍵: URL}。`taken` は既に他社に付いているドメイン。

        **この中で2社に同じドメインが出たら、どちらにも付けない。**
        """
        rows = [(key, name, (code or "")[:4]) for key, name, code in companies]
        self.stats.attempted += len(rows)
        titles = sorted({t for _, name, _ in rows for t in candidate_titles(name)})
        pages: dict[str, str | None] = {}
        failed: set[str] = set()
        for i in range(0, len(titles), BATCH):
            chunk = titles[i : i + BATCH]
            self.stats.requests += 1
            try:
                pages.update(self.fetch(chunk))
            except Exception:  # noqa: BLE001 - 1回の失敗で全体を止めない
                failed.update(chunk)

        found: dict[str, tuple[str, str]] = {}
        for key, name, code in rows:
            titles_for = candidate_titles(name)
            if titles_for and all(t in failed for t in titles_for):
                self.stats.fetch_failed += 1
                continue
            texts = [pages.get(t) for t in titles_for if pages.get(t)]
            if not texts:
                self.stats.no_article += 1
                continue
            boxes = [b for b in (_infobox(t) for t in texts) if b is not None]
            if not boxes:
                self.stats.no_company_infobox += 1
                # どの基礎情報を使っている記事か（銀行・放送局など、会社以外の
                # 形を使う記事を次に読むかの判断材料）
                for t in texts:
                    for m in _ANY_INFOBOX.finditer(t[:4000]):
                        self.stats.other_infoboxes[m.group(1).strip()] += 1
                continue
            mine = [b for b in boxes if code_listed(b, code)]
            if not mine:
                self.stats.code_mismatch += 1
                continue
            url = next((u for u in (external_link(b) for b in mine) if u), None)
            domain = domain_from_url(url) if url else None
            if not url or not domain:
                self.stats.no_external_link += 1
                continue
            if domain in NOT_COMPANY:
                self.stats.not_company_domain += 1
                continue
            if domain in taken:
                self.stats.taken_by_other += 1
                continue
            found[key] = (url, domain)
        uses = Counter(domain for _, domain in found.values())
        out: dict[str, str] = {}
        for key, (url, domain) in found.items():
            if uses[domain] > 1:
                self.stats.taken_by_other += 1
                continue
            out[key] = url
            self.stats.found += 1
            self.stats.samples.append(domain)
        return out
