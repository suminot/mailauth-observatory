"""有価証券報告書の「公告掲載方法」から自社サイトを読む（BACKLOG 15・43）。

電子公告を選んだ会社は、有報の「株式事務の概要」の公告掲載方法に、
**自分で自社サイトの URL を書いている**（例: 「電子公告により行う。…
https://www.example.co.jp/ir/koukoku/」）。政府の開示書類に会社自身が
書いた記載なので、推測ではない。

2026-11-06 の probe（`probe-edinet-koukoku.yml`、run 42268263618）で、
6/26 提出の有報 40 件中 37 件に URL があった（新聞公告 3）。

**URL がそのまま会社のものとは限らない。** 電子公告の掲載を外部に任せる
会社は、その事業者のドメインを書く（probe で、東京海上ホールディングスの
欄に開示書類の印刷会社 www.pronexus.co.jp があった）。そこで、
**そのドメインのトップページに会社の商号が載っていることを確かめてから**
採る（`.co.jp` の推測と同じ確かめ方。`co_jp_guess.CoJpGuesser.confirm`）。

守っていること
  - EDINET は **API（v2、Subscription-Key）だけ**。画面のスクレイピングはしない
  - 1秒に1本まで
  - 過去の日付の書類一覧と、読んだ有報から取り出した URL はキャッシュに置き、
    流し直しで同じものを取り直さない
"""

from __future__ import annotations

import datetime as dt
import html
import io
import json
import re
import zipfile
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..normalize import etld_plus_one

#: 公告の掲載を引き受ける事業者（configs/edinet/koukoku_vendors.yaml）
VENDORS_PATH = "configs/edinet/koukoku_vendors.yaml"


def load_vendors(path: str = VENDORS_PATH) -> frozenset[str]:
    from ..config import load_yaml

    data = load_yaml(path) or {}
    return frozenset(str(v).strip().lower() for v in data.get("vendors") or [] if str(v).strip())


_KNOWN_TLD = None


def _known_registrable(host: str) -> str | None:
    """実在するトップレベルドメインの下の登録ドメイン。

    有報の HTML は URL を途中で折り返すことがあり（`www.nakayama-` / `www.ia`）、
    そのまま丸めると存在しない名前になる。PSL に無い TLD は捨てる。
    """
    global _KNOWN_TLD
    if _KNOWN_TLD is None:
        from publicsuffixlist import PublicSuffixList

        _KNOWN_TLD = PublicSuffixList(accept_unknown=False)
    if _KNOWN_TLD.privatesuffix(host) is None:
        return None
    return etld_plus_one(host)

API_BASE = "https://api.edinet-fsa.go.jp/api/v2"
#: 有価証券報告書（訂正は読まない）
YUHO = "120"
#: 遡る日数。決算期のずれと提出の遅れを見込んで 1 年と少し
LOOKBACK_DAYS = 400
#: これより新しい日の一覧はキャッシュしない（取り下げ・追加がありうる）
FRESH_DAYS = 7
#: 「公告掲載方法」のあとで URL を探す文字数
WINDOW = 400
#: キャッシュの形が変わったら上げる
CACHE_VERSION = 1

_URL = re.compile(r"https?://[A-Za-z0-9.\-]+(?:/[^\s<>\"'）)」]*)?")
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")

#: (パス, パラメータ) → 本文のバイト列。鍵はこの外で付ける
ApiGet = Callable[[str, dict[str, str]], bytes]


def document_text(zip_bytes: bytes) -> str:
    """有報の zip（type=1）から、本文の HTML をつないだ平文。"""
    out: list[str] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for name in sorted(z.namelist()):
            if "PublicDoc" in name and name.endswith(".htm"):
                out.append(z.read(name).decode("utf-8", "replace"))
    text = html.unescape(_TAG.sub(" ", " ".join(out)))
    return _SPACE.sub(" ", text)


def koukoku_domains(text: str) -> list[str]:
    """公告掲載方法の欄に書かれた URL の登録ドメイン（書かれた順、重複なし）。

    欄の見出しは目次などにも出るので、**URL が見つかった最初の出現**を使う。
    """
    start = 0
    while True:
        i = text.find("公告掲載方法", start)
        if i < 0:
            return []
        window = text[i : i + WINDOW]
        out: list[str] = []
        for url in _URL.findall(window):
            host = re.sub(r"^https?://", "", url).split("/")[0].lower().rstrip(".")
            domain = _known_registrable(host)
            if domain and domain not in out:
                out.append(domain)
        if out:
            return out
        start = i + 1


def newspaper_notice(text: str) -> bool:
    """公告掲載方法が新聞（日本経済新聞など）か。URL が無い理由として数える。"""
    i = text.find("公告掲載方法")
    return i >= 0 and "新聞" in text[i : i + WINDOW]


@dataclass
class KoukokuStats:
    attempted: int = 0
    #: EDINET コードが無い（外国会社の一部など）
    no_edinet_code: int = 0
    #: 遡った期間に有報が無かった
    no_yuho: int = 0
    #: 公告掲載方法の欄が無かった
    no_section: int = 0
    #: 新聞公告で URL が無い
    newspaper: int = 0
    #: 欄はあるが URL も新聞も無い
    no_url: int = 0
    #: URL のドメインのページに商号が無かった（公告を外部に任せている会社など）
    name_not_on_page: int = 0
    #: ページが読めず、証明書でも確かめられなかった
    page_unreachable: int = 0
    #: 社名の芯が短すぎてページで確かめられない
    core_too_short: int = 0
    #: 既に他社に付いているドメイン、またはこの中で2社に出たドメイン
    taken_by_other: int = 0
    #: 公告の掲載を引き受ける事業者のドメインだった（koukoku_vendors.yaml）
    vendor: int = 0
    #: ページでは確かめられなかったが、有報に会社が書いた自社ドメインとして採った
    #: （`found` の内数）。委託先でなく、DNS にあり、この中で1社にしか出ないもの
    accepted_from_filing: int = 0
    found: int = 0
    #: 書類一覧を引いた日数・有報を読んだ数・キャッシュに当たった数
    list_requests: int = 0
    documents_read: int = 0
    cache_hit: int = 0
    #: 有報を引けなかった（API の失敗）。**「無い」とは数えない**
    fetch_failed: int = 0
    #: 書類一覧を引けなかった日の数
    list_failed_days: int = 0
    #: 有報が見つからなかったが、一覧を引けなかった日があるので「無い」とは言えない
    yuho_unknown: int = 0
    samples: list[str] = field(default_factory=list)
    sample_not_on_page: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in self.__dict__.items() if not isinstance(v, list)}
        out["sample_found"] = self.samples[:10]
        out["sample_not_on_page"] = self.sample_not_on_page[:10]
        return out


class EdinetKoukoku:
    """起点の無い会社の最新の有報を探し、公告掲載方法の URL を確かめて付ける。

    `api_get` と `confirm` を差し替えられる（検査はネットワークに出ない）。
    `confirm(domain, name_ja, name_en)` は、そのドメインのページに商号が
    載っていれば (domain, "found")、だめなら (None, 理由)。
    """

    def __init__(
        self,
        api_get: ApiGet,
        confirm: Callable[[str, str | None, str | None], tuple[str | None, str]],
        *,
        today: dt.date,
        cache_dir: Path | None = None,
        vendors: Iterable[str] = (),
        exists: Callable[[str], bool] | None = None,
    ) -> None:
        self.api_get = api_get
        self.confirm = confirm
        self.vendors = frozenset(vendors)
        #: ドメインが DNS にあるか。**無ければページで確かめられたものしか採らない**
        self.exists = exists
        self.today = today
        self.cache_dir = cache_dir
        self.stats = KoukokuStats()
        self._from_filing: set[str] = set()

    # -- 書類一覧 ---------------------------------------------------------------

    def _list_cache(self, day: dt.date) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / "lists" / f"{day.isoformat()}.json"

    def _documents_on(self, day: dt.date) -> list[dict[str, str]] | None:
        """その日に出た書類（有報だけ、必要な欄だけ）。引けなければ None。"""
        path = self._list_cache(day)
        if path is not None and path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("version") == CACHE_VERSION:
                    self.stats.cache_hit += 1
                    return list(data["docs"])
            except (OSError, ValueError, KeyError):
                pass
        self.stats.list_requests += 1
        try:
            body = self.api_get(
                "/documents.json", {"date": day.isoformat(), "type": "2"}
            )
            payload = json.loads(body)
        except Exception:  # noqa: BLE001 - 1日の失敗で全体を止めない
            return None
        status = str((payload.get("metadata") or {}).get("status") or "")
        if status != "200":
            return None
        docs = [
            {"docID": r["docID"], "edinetCode": r.get("edinetCode") or ""}
            for r in payload.get("results") or []
            if r.get("docTypeCode") == YUHO
            and str(r.get("withdrawalStatus") or "0") == "0"
            and r.get("docID")
        ]
        if path is not None and (self.today - day).days >= FRESH_DAYS:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"version": CACHE_VERSION, "docs": docs}), encoding="utf-8"
            )
        return docs

    def latest_yuho(self, codes: set[str]) -> dict[str, str]:
        """EDINET コード → 最新の有報の docID。新しい日から遡り、揃ったらやめる。"""
        found: dict[str, str] = {}
        day = self.today
        for _ in range(LOOKBACK_DAYS):
            if len(found) == len(codes):
                break
            if day.weekday() < 5:
                docs = self._documents_on(day)
                if docs is None:
                    self.stats.list_failed_days += 1
                else:
                    for d in docs:
                        code = d["edinetCode"]
                        if code in codes and code not in found:
                            found[code] = d["docID"]
            day -= dt.timedelta(days=1)
        return found

    # -- 1件の有報 ---------------------------------------------------------------

    def _doc_cache(self, doc_id: str) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / "docs" / f"{doc_id}.json"

    def _read(self, doc_id: str) -> tuple[list[str], bool, bool] | None:
        """(公告の URL のドメイン, 欄があったか, 新聞か)。引けなければ None。"""
        path = self._doc_cache(doc_id)
        if path is not None and path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("version") == CACHE_VERSION:
                    self.stats.cache_hit += 1
                    # 古い読み方で残った、存在しない TLD の名前を落とす
                    domains = [d for d in data["domains"] if _known_registrable(d)]
                    return domains, bool(data["section"]), bool(data["paper"])
            except (OSError, ValueError, KeyError):
                pass
        self.stats.documents_read += 1
        try:
            text = document_text(self.api_get(f"/documents/{doc_id}", {"type": "1"}))
        except Exception:  # noqa: BLE001
            return None
        out = (koukoku_domains(text), "公告掲載方法" in text, newspaper_notice(text))
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"version": CACHE_VERSION, "domains": out[0],
                            "section": out[1], "paper": out[2]}),
                encoding="utf-8",
            )
        return out

    # -- 本体 --------------------------------------------------------------------

    def websites_for(
        self,
        companies: Iterable[tuple[str, str | None, str | None, str | None]],
        taken: set[str],
    ) -> dict[str, str]:
        """(鍵, EDINET コード, 社名, 英字社名) → {鍵: ドメイン}。

        `taken` は既に他社に付いているドメイン。**この中で2社に同じドメインが
        出たら、どちらにも付けない**（親子で同じ公告ページを使う会社がある）。
        """
        rows = list(companies)
        self.stats.attempted += len(rows)
        codes = {code for _, code, _, _ in rows if code}
        self.stats.no_edinet_code += sum(1 for _, code, _, _ in rows if not code)
        latest = self.latest_yuho(codes) if codes else {}

        found: dict[str, str] = {}
        for key, code, name, name_en in rows:
            if not code:
                continue
            doc_id = latest.get(code)
            if doc_id is None:
                # **一覧を引けなかった日があれば「無い」とは言えない**（原則5）
                if self.stats.list_failed_days:
                    self.stats.yuho_unknown += 1
                else:
                    self.stats.no_yuho += 1
                continue
            read = self._read(doc_id)
            if read is None:
                self.stats.fetch_failed += 1
                continue
            domains, section, paper = read
            if not domains:
                if not section:
                    self.stats.no_section += 1
                elif paper:
                    self.stats.newspaper += 1
                else:
                    self.stats.no_url += 1
                continue
            outcome = "none"
            fallback: str | None = None
            for domain in domains:
                if domain in self.vendors:
                    outcome = "vendor"
                    continue
                if domain in taken:
                    outcome = "taken"
                    continue
                got, kind = self.confirm(domain, name, name_en)
                if got:
                    found[key] = got
                    outcome = "found"
                    break
                outcome = kind
                if fallback is None and kind in ("not_on_page", "unreachable", "core_too_short"):
                    fallback = domain
                if kind == "not_on_page" and len(self.stats.sample_not_on_page) < 10:
                    self.stats.sample_not_on_page.append(f"{domain} ← {name}")
            if outcome != "found" and fallback is not None and self.exists is not None:
                # **ページで確かめられなくても、会社が政府の開示書類に自分で
                # 書いた自社ドメインとして採る。** probe（2026-11-06）で、公式
                # サイトの分かっている会社の有報 1,347 件のうち 1,229 件は公式
                # サイトそのもの、違う 118 件も委託先 1 社（13 件）を除けば
                # 会社自身の別ドメインだった。委託先は一覧で除く
                if self.exists(fallback):
                    found[key] = fallback
                    self._from_filing.add(key)
                    outcome = "found"
            if outcome == "vendor":
                self.stats.vendor += 1
            if outcome == "taken":
                self.stats.taken_by_other += 1
            elif outcome == "not_on_page":
                self.stats.name_not_on_page += 1
            elif outcome == "unreachable":
                self.stats.page_unreachable += 1
            elif outcome == "core_too_short":
                self.stats.core_too_short += 1

        uses = Counter(found.values())
        out: dict[str, str] = {}
        for key, domain in found.items():
            if uses[domain] > 1:
                self.stats.taken_by_other += 1
                continue
            out[key] = domain
            self.stats.found += 1
            if key in self._from_filing:
                self.stats.accepted_from_filing += 1
            self.stats.samples.append(domain)
        return out


def live_api(key: str, *, user_agent: str) -> ApiGet:
    """EDINET API v2 を 1 秒に 1 本までで引く。鍵はクエリに付ける（API の作法）。"""
    import time

    import httpx

    client = httpx.Client(timeout=120, headers={"User-Agent": user_agent})
    last = [0.0]

    def get(path: str, params: dict[str, str]) -> bytes:
        wait = 1.0 - (time.monotonic() - last[0])
        if wait > 0:
            time.sleep(wait)
        last[0] = time.monotonic()
        resp = client.get(API_BASE + path, params={**params, "Subscription-Key": key})
        resp.raise_for_status()
        return resp.content

    return get
