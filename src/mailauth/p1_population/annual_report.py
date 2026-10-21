"""年次報告書の本文から、その会社が自分で書いた公式サイトを取る（BACKLOG 15）。

**SEC の submissions は website 欄を持たない**（2026-09、6,068社全件で0件）。
米国上場企業の 64% が起点ドメインを持たないのは、ほぼこれが原因である。

だが 10-K には SEC の求めで「Available Information」の節があり、
**「Our website address is www.example.com」と会社が自分で書いている。**
2026-10-18 に無作為の30社で試すと、年次報告書のある22社のうち13社で
自社ドメインが取れた（`www.` 付きの書き方だけを見た段階で）。

守っていること
  - **「自社の」と書いてあるものだけを取る。** `our website` / `the Company's
    website` の直後にあるドメインだけ。文書中の任意の URL は拾わない ──
    sec.gov や監査法人、IR 代行のドメインが必ず混ざる
  - **第三者のドメインは捨てる**（`THIRD_PARTY`）。「our website or the SEC's
    website at www.sec.gov」のような書き方があるため
  - **一番多く書かれたものを採る。** 1つの文書に IR 専用ドメインと本体の
    ドメインが並ぶことがある
  - **取れなかったことと、引いていないことを分ける**（原則5）
  - 結果は提出書類の受付番号で持ち越す。**提出済みの書類は変わらない。**
"""

from __future__ import annotations

import html
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..normalize import etld_plus_one

#: 年次報告書として読む様式。米国企業は 10-K、外国企業は 20-F / 40-F
ANNUAL_FORMS = ("10-K", "20-F", "40-F")

ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"

#: 本文の先頭から読む量。「Available Information」は Item 1 にあり、
#: 前のほうに来る。10-K は丸ごとだと 10 MB を超えることがある
MAX_BYTES = 4_000_000

#: 本文の読み方を変えたら上げる。キャッシュに書いた結果は、版が違えば読み直す
#: ── 古い読み方の結果を黙って使い続けないため
EXTRACTOR_VERSION = 2

#: 「自社の」サイトであることを示す書き方。**ここが所有の根拠になる。**
#:
#: **「its website」は読まない。** 「its」は直前に出てきた別の会社を指すことが
#: ある。2026-10-21 の全数実行で、ビットコイン ETF に指数提供会社の
#: `cfbenchmarks.com` が付いていた（「CF Benchmarks … its website」）
_SELF_PHRASE = re.compile(
    r"\b(?:our|the\s+company[’']?s)\s+"
    r"(?:corporate\s+|internet\s+|investor\s+relations\s+|principal\s+)?"
    r"(?:website|web\s+site|internet\s+(?:website|site|address)|home\s*page)"
    r"(?:\s+address)?",
    re.I,
)

#: 句の直後でドメインを探す範囲（文字数）。「Our website address is
#: www.example.com, where we make available …」の形がほとんど
_WINDOW = 160

_HOST = re.compile(
    r"(?<![@\w.-])(?:https?://)?((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24})"
    r"(?![\w-])",
    re.I,
)

#: ドメインに見えるが、TLD として扱わない語。文の切れ目が詰まった
#: 「10-K.The」や「Inc.All」が拾われないようにする
_TLD_ALLOW = re.compile(
    r"^(?:com|net|org|io|ai|co|us|biz|info|bank|inc|group|global|health|tech|"
    r"energy|capital|financial|insurance|holdings|gov|edu|[a-z]{2})$",
    re.I,
)

#: 「自社の」の直後に書かれていても、その会社のものではないドメイン
THIRD_PARTY = frozenset(
    {
        "sec.gov",
        "edgar-online.com",
        "q4cdn.com",
        "q4ir.com",
        "gcs-web.com",
        "globenewswire.com",
        "businesswire.com",
        "prnewswire.com",
        "nasdaq.com",
        "nyse.com",
        "linkedin.com",
        "twitter.com",
        "x.com",
        "facebook.com",
        "instagram.com",
        "youtube.com",
        "sedar.com",
        "sedarplus.ca",
        # 2026-10-21 の全数実行で見つかった IR 代行・指数提供会社
        "investorroom.com",
        "irpass.com",
        "cfbenchmarks.com",
    }
)

#: ファイル名の拡張子。「Form10-K.htm」がドメインに見えるのを防ぐ
_FILE_SUFFIXES = (".htm", ".html", ".pdf", ".xml", ".txt", ".jpg", ".png", ".gif")


@dataclass
class AnnualReport:
    """submissions から見つけた、最新の年次報告書。"""

    cik: str
    form: str
    accession: str
    document: str
    filing_date: str | None = None

    @property
    def url(self) -> str:
        return ARCHIVE_URL.format(
            cik=int(self.cik), accession=self.accession.replace("-", ""), document=self.document
        )


@dataclass
class AnnualReportStats:
    #: 年次報告書を読もうとした企業（起点が無かった企業）
    attempted: int = 0
    #: submissions に年次報告書が無かった（SPAC・ADR・新規上場など）
    no_annual_report: int = 0
    fetched: int = 0
    cache_hit: int = 0
    fetch_failed: int = 0
    #: 読み方が変わったので読み直したキャッシュ
    stale_cache: int = 0
    found: int = 0
    #: 読めたが「自社の」サイトの記述が無かった
    not_stated: int = 0
    #: 「自社の」の直後にあったが、第三者のドメインだったので捨てた件数
    third_party_dropped: int = 0
    by_form: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "no_annual_report": self.no_annual_report,
            "fetched": self.fetched,
            "cache_hit": self.cache_hit,
            "fetch_failed": self.fetch_failed,
            "stale_cache": self.stale_cache,
            "found": self.found,
            "not_stated": self.not_stated,
            "third_party_dropped": self.third_party_dropped,
            "by_form": dict(sorted(self.by_form.items())),
            "notes": self.notes[:5],
        }


def latest_annual_report(cik: str, submissions: dict[str, Any] | None) -> AnnualReport | None:
    """submissions の `filings.recent` から最新の年次報告書を探す。

    `recent` は新しい順に並んでいる。訂正（10-K/A）は本文が一部だけの
    ことが多いので読まない。
    """
    recent = ((submissions or {}).get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    accessions = recent.get("accessionNumber") or []
    documents = recent.get("primaryDocument") or []
    dates = recent.get("filingDate") or []
    for i, form in enumerate(forms):
        if form not in ANNUAL_FORMS:
            continue
        if i >= len(accessions) or i >= len(documents) or not documents[i]:
            continue
        return AnnualReport(
            cik=cik,
            form=form,
            accession=accessions[i],
            document=documents[i],
            filing_date=dates[i] if i < len(dates) else None,
        )
    return None


def _plain_text(raw: str) -> str:
    """タグを空白に置き換え、実体参照を戻し、空白を詰める。

    **タグは空白に置き換える。** 消すと `website</td><td>www.x.com` が
    `websitewww.x.com` になり、`Inc.</p><p>The` が `Inc.The` になる。
    """
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text)


#: 2文字の TLD のうち、国を表さない用途で広く使われるもの
_GENERIC_CC = frozenset({"us", "io", "ai", "co", "tv", "me"})


def _candidate(token: str) -> tuple[str, str] | None:
    """ドメインに見える語を (書かれたホスト, eTLD+1) にする。使えなければ None。

    **2文字の TLD は、`www.` か `http(s)://` が付いているときだけ通す。**
    文の切れ目が詰まった「Corp.In addition」が `corp.in` に見えるため。
    """
    explicit = token.lower().startswith(("http://", "https://", "www."))
    host = re.sub(r"^https?://", "", token.lower()).rstrip(".")
    if host.endswith(_FILE_SUFFIXES):
        return None
    tld = host.rsplit(".", 1)[-1]
    if not _TLD_ALLOW.match(tld):
        return None
    if len(tld) == 2 and tld not in _GENERIC_CC and not explicit:
        return None
    domain = etld_plus_one(host)
    return (host, domain) if domain else None


def stated_domains(raw: str) -> tuple[Counter[str], dict[str, str], int]:
    """本文中で「自社の」サイトとして書かれたドメインを数える。

    戻り値は (eTLD+1 ごとの回数, eTLD+1 → 最初に書かれたホスト,
    第三者として捨てた回数)。**ホストは書かれたとおりに持つ。** `www.` を
    こちらで足すと、存在しない名前を作ることがある。
    """
    text = _plain_text(raw)
    found: Counter[str] = Counter()
    hosts: dict[str, str] = {}
    dropped = 0
    for phrase in _SELF_PHRASE.finditer(text):
        window = text[phrase.end() : phrase.end() + _WINDOW]
        for m in _HOST.finditer(window):
            cand = _candidate(m.group(0))
            if not cand:
                continue
            host, domain = cand
            if domain in THIRD_PARTY or domain.endswith(".gov"):
                dropped += 1
                continue
            found[domain] += 1
            hosts.setdefault(domain, host)
            # **句ごとに最初の1つだけ。** 「our website at www.a.com and
            # the SEC's website at www.sec.gov」の後半を数えない
            break
    return found, hosts, dropped


def pick_domain(found: Counter[str]) -> str | None:
    """一番多く書かれたドメイン。同数なら先に書かれたもの（Counter の順）。"""
    if not found:
        return None
    best = max(found.values())
    return next(d for d, n in found.items() if n == best)


#: 社名とドメイン名を突き合わせるときに見ない語（どの会社にも付く）
_NAME_STOPWORDS = frozenset(
    {
        "inc", "corp", "corporation", "company", "holdings", "holding", "group",
        "ltd", "limited", "plc", "the", "and", "international", "technologies",
        "technology", "global", "partners", "trust", "fund", "acquisition",
    }
)


def name_matches_domain(name: str | None, domain: str | None) -> bool:
    """ドメイン名（最初のラベル）に社名の語が入っているか。

    **複数社に共用されていたドメインを、どの会社に残すか**を決めるのに使う。
    `brookfield.com` は Brookfield の各社に残し、IR 代行のドメインは
    どの会社にも残さない。広く当てすぎないよう、3文字以上の語だけを見る。
    """
    if not name or not domain:
        return False
    label = domain.split(".")[0].replace("-", "").lower()
    tokens = [
        t
        for t in re.split(r"[^a-z0-9]+", name.lower())
        if len(t) >= 3 and t not in _NAME_STOPWORDS
    ]
    return any(t in label or (len(label) >= 4 and label in t) for t in tokens)


class AnnualReportWebsites:
    """年次報告書から公式サイトを取る。結果は受付番号で持ち越す。

    取得は `SecEdgarClient` に任せる ── User-Agent と 10 req/s 未満の
    作法はあちらで守っている。
    """

    def __init__(self, client, *, cache_dir: Path | None = None) -> None:
        self.client = client
        self.cache_dir = cache_dir or (client.cache_dir / "annual_website")
        self.stats = AnnualReportStats()

    def _cache_path(self, report: AnnualReport) -> Path:
        return self.cache_dir / f"{report.accession}.json"

    def website_for(self, report: AnnualReport | None) -> str | None:
        self.stats.attempted += 1
        if report is None:
            self.stats.no_annual_report += 1
            return None
        self.stats.by_form[report.form] = self.stats.by_form.get(report.form, 0) + 1

        path = self._cache_path(report)
        cached: dict[str, Any] | None = None
        if path.is_file():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cached = None
        if cached is not None and cached.get("extractor_version") != EXTRACTOR_VERSION:
            # 読み方が変わった。古い結果は使わない
            cached = None
            self.stats.stale_cache += 1
        if cached is not None:
            self.stats.cache_hit += 1
            domain = cached.get("domain")
            host = cached.get("host")
            dropped = int(cached.get("third_party_dropped") or 0)
        else:
            try:
                raw = self.client.get_text(report.url, max_bytes=MAX_BYTES)
            except Exception as exc:  # noqa: BLE001 - 1社の失敗で母集団を止めない
                self.stats.fetch_failed += 1
                if len(self.stats.notes) < 10:
                    self.stats.notes.append(f"{report.accession}: {exc}")
                return None
            if raw is None:
                self.stats.fetch_failed += 1
                return None
            self.stats.fetched += 1
            found, hosts, dropped = stated_domains(raw)
            domain = pick_domain(found)
            host = hosts.get(domain) if domain else None
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "cik": report.cik,
                        "form": report.form,
                        "filing_date": report.filing_date,
                        "domain": domain,
                        "host": host,
                        "extractor_version": EXTRACTOR_VERSION,
                        "counts": dict(found),
                        "third_party_dropped": dropped,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

        self.stats.third_party_dropped += dropped
        if not domain:
            self.stats.not_stated += 1
            return None
        self.stats.found += 1
        return f"https://{host or domain}"
