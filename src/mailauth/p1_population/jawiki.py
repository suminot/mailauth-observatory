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
#: 検索で見る記事の数
SEARCH_LIMIT = 5

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


_SQUASH = re.compile(r"[\s・･\-‐－.,，、。'’&＆]+")


def _squash(text: str) -> str:
    return _SQUASH.sub("", unicodedata.normalize("NFKC", text or "")).lower()


def name_on_article(title: str, infobox: str, name: str) -> bool:
    """記事の名前か、基礎情報の「社名」に、その会社の社名（法人格を除く）があるか。

    検索で見つけた記事にだけ使う。**コードが合っても、名前が無ければ採らない**
    （上場廃止した会社のコードが、別の会社に振り直されていることがある）。
    """
    base = _squash(_LEGAL_FORM.sub("", unicodedata.normalize("NFKC", name or "").strip()))
    if len(base) < 2:
        return False
    hay = _squash(title) + "|" + _squash(_param(infobox, "社名") or "")
    return base in hay


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
    #: 記事の名前で当たらなかった会社を、社名と証券コードで検索した数
    searched: int = 0
    #: そのうち検索で取れた数（`found` の内数）
    found_by_search: int = 0
    #: 検索で当たった記事のコードは合ったが、社名が無かった数
    search_name_mismatch: int = 0
    #: 検索を引けなかった数。**「見つからなかった」とは数えない**
    search_failed: int = 0
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
            "searched": self.searched,
            "found_by_search": self.found_by_search,
            "search_name_mismatch": self.search_name_mismatch,
            "search_failed": self.search_failed,
            "sample_found": self.samples[:10],
            "other_infoboxes": dict(self.other_infoboxes.most_common(10)),
        }


class _Api:
    """MediaWiki の API を1本ずつ、1秒に1本までで引く。記事の取得と検索で共有する。"""

    def __init__(self) -> None:
        import httpx

        from .wikidata import user_agent

        self._httpx = httpx
        self.client = httpx.Client(headers={"User-Agent": user_agent()}, timeout=30)
        self.last = 0.0

    def query(self, params: dict[str, str]) -> dict[str, Any]:
        httpx = self._httpx
        for wait in (*RETRY_WAITS, None):
            pause = INTERVAL_SEC - (time.monotonic() - self.last)
            if pause > 0:
                time.sleep(pause)
            self.last = time.monotonic()
            try:
                resp = self.client.get(API_URL, params=params)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise httpx.HTTPStatusError("retry", request=resp.request, response=resp)
                resp.raise_for_status()
                return resp.json().get("query", {})
            except (httpx.TransportError, httpx.HTTPStatusError, ValueError):
                if wait is None:
                    raise
                time.sleep(wait)
        return {}  # pragma: no cover - 上の for で必ず返るか投げる


def live_api() -> _Api:
    """User-Agent は Wikidata と同じ（連絡先 `MAILAUTH_CONTACT_EMAIL` を入れる）。"""
    return _Api()


def live_fetch(api: _Api | None = None) -> Callable[[list[str]], dict[str, str | None]]:
    """記事名 → 本文（無ければ None）。リダイレクトはたどる。"""
    api = api or live_api()

    def fetch(titles: list[str]) -> dict[str, str | None]:
        query = api.query({
            "action": "query", "format": "json", "formatversion": "2",
            "prop": "revisions", "rvprop": "content", "rvslots": "main",
            "redirects": "1", "titles": "|".join(titles),
        })
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


def search_query(name: str, code: str) -> str | None:
    """記事を探す検索式。**社名と、記事の原文にある証券コードと、会社の基礎情報。**

    4桁のコードは年（2021 など）と区別が付かないので、コードだけでは探さない。
    """
    base = _LEGAL_FORM.sub("", unicodedata.normalize("NFKC", name or "").strip()).strip()
    base = re.sub(r"[\"()（）]", " ", base).strip()
    if not base or not code:
        return None
    return f'{base} insource:"{code}" hastemplate:"基礎情報 会社"'


def live_search(api: _Api | None = None) -> Callable[[str], list[str]]:
    """検索式 → 記事名（上位 SEARCH_LIMIT 件）。"""
    api = api or live_api()

    def search(srsearch: str) -> list[str]:
        query = api.query({
            "action": "query", "format": "json", "formatversion": "2",
            "list": "search", "srsearch": srsearch, "srnamespace": "0",
            "srlimit": str(SEARCH_LIMIT), "srprop": "",
        })
        return [hit["title"] for hit in query.get("search", [])]

    return search


class JawikiWebsites:
    """起点の無い会社に、日本語版の記事の基礎情報から公式サイトを付ける。

    1段目は記事の名前（社名から作る）で引く。**そこで記事が無かったか、別の
    会社の記事だった会社だけ**、2段目で社名と証券コードで検索し、上位の記事
    を読む。2段目で採るのは、市場情報のコードが合い、**かつ**記事の名前か
    基礎情報の社名にその社名があるものだけ（BACKLOG 40）。

    `fetch` と `search` を渡せるのは検査のため（ネットワークに出ない）。
    `search` が無ければ2段目はしない。
    """

    def __init__(
        self,
        fetch: Callable[[list[str]], dict[str, str | None]],
        search: Callable[[str], list[str]] | None = None,
    ) -> None:
        self.fetch = fetch
        self.search = search
        self.stats = JawikiStats()
        #: 2段目の検索で取れた会社（同じドメインの衝突で落としたときに数え直す）
        self._by_search: set[str] = set()

    def _fetch_all(self, titles: Iterable[str], pages: dict[str, str | None],
                   failed: set[str]) -> None:
        todo = sorted({t for t in titles if t not in pages and t not in failed})
        for i in range(0, len(todo), BATCH):
            chunk = todo[i : i + BATCH]
            self.stats.requests += 1
            try:
                pages.update(self.fetch(chunk))
            except Exception:  # noqa: BLE001 - 1回の失敗で全体を止めない
                failed.update(chunk)

    def websites_for(
        self, companies: Iterable[tuple[str, str, str | None]], taken: set[str]
    ) -> dict[str, str]:
        """(鍵, 社名, 証券コード) → {鍵: URL}。`taken` は既に他社に付いているドメイン。

        **この中で2社に同じドメインが出たら、どちらにも付けない。**
        """
        rows = [(key, name, (code or "")[:4]) for key, name, code in companies]
        self.stats.attempted += len(rows)
        pages: dict[str, str | None] = {}
        failed: set[str] = set()
        self._fetch_all((t for _, name, _ in rows for t in candidate_titles(name)),
                        pages, failed)

        found: dict[str, tuple[str, str]] = {}
        #: 2段目に回す会社と、1段目で数えた欄
        retry: list[tuple[str, str, str, str]] = []
        for key, name, code in rows:
            titles_for = candidate_titles(name)
            if titles_for and all(t in failed for t in titles_for):
                self.stats.fetch_failed += 1
                continue
            texts = [pages.get(t) for t in titles_for if pages.get(t)]
            if not texts:
                self.stats.no_article += 1
                retry.append((key, name, code, "no_article"))
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
                retry.append((key, name, code, "code_mismatch"))
                continue
            self._take(key, mine, taken, found)

        if self.search is not None and retry:
            self._second_pass(retry, taken, found, pages, failed)

        uses = Counter(domain for _, domain in found.values())
        out: dict[str, str] = {}
        for key, (url, domain) in found.items():
            if uses[domain] > 1:
                self.stats.taken_by_other += 1
                if key in self._by_search:
                    self.stats.found_by_search -= 1
                continue
            out[key] = url
            self.stats.found += 1
            self.stats.samples.append(domain)
        return out

    def _take(self, key: str, boxes: list[str], taken: set[str],
              found: dict[str, tuple[str, str]]) -> bool:
        url = next((u for u in (external_link(b) for b in boxes) if u), None)
        domain = domain_from_url(url) if url else None
        if not url or not domain:
            self.stats.no_external_link += 1
            return False
        if domain in NOT_COMPANY:
            self.stats.not_company_domain += 1
            return False
        if domain in taken:
            self.stats.taken_by_other += 1
            return False
        found[key] = (url, domain)
        return True

    def _second_pass(self, retry: list[tuple[str, str, str, str]], taken: set[str],
                     found: dict[str, tuple[str, str]], pages: dict[str, str | None],
                     failed: set[str]) -> None:
        assert self.search is not None
        hits: dict[str, list[str]] = {}
        for key, name, code, _ in retry:
            q = search_query(name, code)
            if q is None:
                continue
            self.stats.searched += 1
            self.stats.requests += 1
            try:
                hits[key] = list(self.search(q))[:SEARCH_LIMIT]
            except Exception:  # noqa: BLE001 - 1回の失敗で全体を止めない
                self.stats.search_failed += 1
        self._fetch_all((t for ts in hits.values() for t in ts), pages, failed)
        for key, name, code, bucket in retry:
            titles = hits.get(key) or []
            matched = [
                (t, b) for t in titles if pages.get(t)
                for b in [_infobox(pages[t] or "")] if b is not None and code_listed(b, code)
            ]
            if not matched:
                continue
            mine = [b for t, b in matched if name_on_article(t, b, name)]
            if not mine:
                self.stats.search_name_mismatch += 1
                continue
            # 1段目の欄から外して、採れたかどうかで数え直す
            setattr(self.stats, bucket, getattr(self.stats, bucket) - 1)
            if self._take(key, mine, taken, found):
                self.stats.found_by_search += 1
                self._by_search.add(key)
