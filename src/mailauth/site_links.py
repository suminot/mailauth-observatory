"""公式サイトのトップページのリンク先から、同じ会社の別ドメインを拾う（BACKLOG 44）。

攻撃面管理（ASM）でよく使う手がかりで、会社は自分のトップページに、
ブランドサイト・通販サイト・採用サイト・海外サイトへのリンクを張っている。
そのドメインでメールを送っていることがある（例：nifco.com のトップから
nifco-fastener.com・nifcodamper.com、都築電気 tsuzuki.co.jp から tsuzuki.jp）。

**リンクを張っているだけでは同じ会社とは言えない**（取引先・SNS・業界団体・
証券取引所にもリンクする）。そこで次をすべて満たすものだけを採る。

  1. **多くの会社が張っているドメインは捨てる。** この実行の中で
     `SHARED_LIMIT` 社以上がリンクしているドメインは第三者のもの
     （SNS・JPX・地図・広告など）とみなす
  2. **共用ホスティングの名前は捨てる**（PSL の PRIVATE 節。`*.sakura.tv` など）
  3. **MX がある**（メールを受けるドメインだけが計測の対象になる）
  4. **そのドメインのトップページのタイトルに、その会社の商号がある**
     （国内は商号の形 `株式会社〇〇` などで。`.co.jp` の推測の strict と同じ。
     米国は英字社名がタイトルにあること）

2026-11-06 の手元の試験（国内 40 社）：リンク先 232 ドメインから 10 ドメイン
（7 社）が確かめられた。読むのはトップページだけで、深くはたどらない。
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from .normalize import etld_plus_one
from .ordered import in_input_order

#: この実行の中で、これ以上の会社がリンクしているドメインは第三者のもの
SHARED_LIMIT = 3
#: 1社あたり確かめに行くリンク先の数
MAX_CANDIDATES = 15
DEFAULT_CONCURRENCY = 8

_HREF = re.compile(r"""href\s*=\s*["']?(https?://[^"'\s>]+)""", re.IGNORECASE)


def _icann_psl():
    from publicsuffixlist import PublicSuffixList

    return PublicSuffixList(only_icann=True)


_ICANN = None


def is_private_hosting(host: str) -> bool:
    """PSL の PRIVATE 節の下の名前か（`x.sakura.tv`・`x.github.io` など）。"""
    global _ICANN
    if _ICANN is None:
        _ICANN = _icann_psl()
    h = host.strip().lower().rstrip(".")
    return (_ICANN.privatesuffix(h) or None) != etld_plus_one(h)


_KNOWN = None


def _known_tld(host: str) -> bool:
    """PSL に載っている TLD の下の名前か（途中で切れた URL や誤記を捨てる）。"""
    global _KNOWN
    if _KNOWN is None:
        from publicsuffixlist import PublicSuffixList

        _KNOWN = PublicSuffixList(accept_unknown=False)
    return _KNOWN.privatesuffix(host) is not None


def link_domains(page: str, own: str) -> list[str]:
    """ページの絶対リンクの登録ドメイン（出てきた順、重複なし、自分と共用ホスティングは除く）。"""
    out: list[str] = []
    for url in _HREF.findall(page or ""):
        host = re.sub(r"^https?://", "", url, flags=re.IGNORECASE).split("/")[0]
        # **全角を半角に揃える。** HTML に `www.murata.coｍ` のような全角の
        # 混じったリンクがあり、そのまま引いて P2 ごと落ちた（2026-11-06）
        host = unicodedata.normalize("NFKC", host)
        host = host.split("?")[0].split("#")[0].split(":")[0].lower().rstrip(".")
        if "@" in host or not host or not host.isascii():
            continue
        if not _known_tld(host):
            continue
        domain = etld_plus_one(host)
        if not domain or domain == own or domain in out or is_private_hosting(host):
            continue
        out.append(domain)
    return out


@dataclass
class SiteLinkStats:
    #: トップページを読みに行った会社
    sites: int = 0
    #: 読めなかった
    unreadable: int = 0
    #: リンク先の登録ドメイン（重複あり、会社ごと）
    link_domains: int = 0
    #: 多くの会社が張っていて第三者とみなしたもの
    shared_skipped: int = 0
    #: MX が無いので測らないもの
    no_mx: int = 0
    #: 確かめに行った
    checked: int = 0
    #: ページに商号が無かった・読めなかった
    not_same_company: int = 0
    #: 確かめられた（企業ごとの延べ）
    found: int = 0
    entities_with_found: int = 0
    #: 持ち時間を使い切って読まなかった（トップページ・確かめ）。**「無い」ではない**
    budget_skipped: int = 0
    elapsed_sec: float = 0.0
    samples: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in self.__dict__.items() if k != "samples"}
        out["sample_found"] = self.samples[:10]
        return out


@dataclass
class SiteCompany:
    official: str
    name: str | None
    name_en: str | None
    country: str


def names_company(page: str, company: SiteCompany) -> bool:
    """リンク先のページが、その会社のものと言えるか（タイトルで見る）。"""
    from .p1_population.co_jp_guess import (
        MIN_CORE_CHARS,
        name_core,
        page_names_company,
        title_names_company_en,
    )

    if company.country == "JP":
        core = name_core(company.name)
        if len(core) >= MIN_CORE_CHARS and page_names_company(page, core, strict=True):
            return True
        return bool(company.name_en) and title_names_company_en(page, company.name_en)
    return title_names_company_en(page, company.name_en or company.name)


class SiteLinkSource:
    """`fetch(url) -> 本文 | None` と `has_mx(domain) -> bool` を差し替えられる。"""

    def __init__(
        self,
        fetch: Callable[[str], str | None],
        has_mx: Callable[[str], bool],
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        shared_limit: int = SHARED_LIMIT,
        max_candidates: int = MAX_CANDIDATES,
        budget_sec: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.fetch = fetch
        self.has_mx = has_mx
        self.concurrency = max(int(concurrency), 1)
        self.shared_limit = shared_limit
        self.max_candidates = max_candidates
        #: **この経路にかける持ち時間（秒）。0 で無制限。** 国内 2026-11-07 の
        #: 流し直しで P2 が 3 時間かかった。超えたら残りは読まない
        self.budget_sec = float(budget_sec or 0)
        self.clock = clock
        self._deadline: float | None = None
        self.stats = SiteLinkStats()

    def _over(self) -> bool:
        return self._deadline is not None and self.clock() > self._deadline

    def _top(self, domain: str) -> str | None:
        for url in (f"https://www.{domain}/", f"https://{domain}/"):
            try:
                page = self.fetch(url)
            except Exception:  # noqa: BLE001 - 1つの壊れた名前で全体を止めない
                page = None
            if page:
                return page
        return None

    def discover(self, companies: Iterable[SiteCompany]) -> dict[str, list[str]]:
        """起点ドメイン → 同じ会社と確かめられた別ドメイン。"""
        items = list({c.official: c for c in companies if c.official}.values())
        self.stats.sites += len(items)
        started = self.clock()
        if self.budget_sec > 0:
            self._deadline = started + self.budget_sec

        def links(company: SiteCompany) -> list[str] | None | str:
            if self._over():
                return "budget"
            page = self._top(company.official)
            return None if page is None else link_domains(page, company.official)

        found_links: dict[str, list[str]] = {}
        for company, got in zip(
            items,
            in_input_order(links, items, concurrency=self.concurrency, window=256),
            strict=True,
        ):
            if got == "budget":
                self.stats.budget_skipped += 1
                continue
            if got is None:
                self.stats.unreadable += 1
                continue
            found_links[company.official] = got
            self.stats.link_domains += len(got)

        # **多くの会社が張っているものは第三者。** 1社の中の重複は数えない
        spread = Counter(d for ds in found_links.values() for d in set(ds))
        shared = {d for d, n in spread.items() if n >= self.shared_limit}
        officials = {c.official for c in items}

        tasks: list[tuple[SiteCompany, str]] = []
        for company in items:
            cands = []
            for d in found_links.get(company.official, []):
                if d in shared:
                    self.stats.shared_skipped += 1
                    continue
                if d in officials:
                    # 別の会社の起点（親子・取引先）。その会社のものとして測る
                    continue
                cands.append(d)
            tasks.extend((company, d) for d in cands[: self.max_candidates])

        def check(task: tuple[SiteCompany, str]) -> str:
            company, domain = task
            if self._over():
                return "budget"
            if not self.has_mx(domain):
                return "no_mx"
            page = self._top(domain)
            if page is None or not names_company(page, company):
                return "not_same"
            return "found"

        out: dict[str, list[str]] = {}
        for (company, domain), kind in zip(
            tasks,
            in_input_order(check, tasks, concurrency=self.concurrency, window=256),
            strict=True,
        ):
            if kind == "budget":
                self.stats.budget_skipped += 1
                continue
            if kind == "no_mx":
                self.stats.no_mx += 1
                continue
            self.stats.checked += 1
            if kind != "found":
                self.stats.not_same_company += 1
                continue
            out.setdefault(company.official, []).append(domain)
            self.stats.found += 1
            if len(self.stats.samples) < 10:
                self.stats.samples.append(f"{company.official} → {domain}")
        self.stats.entities_with_found = len(out)
        self.stats.elapsed_sec = round(self.clock() - started, 1)
        return out
