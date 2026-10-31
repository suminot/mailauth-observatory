"""英字社名から .co.jp を推測し、そのサイトに社名が載っているかで確かめる（BACKLOG 15）。

国内の起点なしを埋める経路。gBizINFO と Wikidata のあとに、**まだ空欄の企業
だけ**に使う。2026-09 の国内計測では、測ったドメインが1つも無い上場企業が
609 社あり、うち 453 社は EDINET に英字社名があった。

なぜ .co.jp か
  - .co.jp は**1つの組織が1つしか持てない**（JPRS の登録規則）
  - 上場企業の大半は英字社名の語をそのまま .co.jp にしている
    （MURAKI CORPORATION → muraki.co.jp）

なぜ「サイトに社名が載っているか」で確かめるか
  - **推測は推測でしかない。** 短い語ほど他社が持っている（2026-10-31 の
    試験で、area.co.jp は Area,Inc.、plus.co.jp は PLUS CORP. のものだった。
    探していたのはエリアクエストとプラスアルファ・コンサルティング）
  - WHOIS の登録組織名でも確かめられるが、WhoisXML API の規約は取得した
    データとその派生物を第三者に渡すことを禁じている。このリポジトリは
    公開なので使わない。**その会社自身が公開しているページ**で確かめる

守っていること
  - 確かめる語は**日本語の商号**（株式会社・ホールディングス等を除いた
    部分、3文字以上）。英字の語は他社のサイトにも出やすい
  - 1社につきページは推測が DNS に実在したものだけ、各1回。連絡先つきの
    User-Agent を付ける（SEC と同じ作法）
  - 試験（2026-10-31、30社）：WHOIS で確かめた 12 社と一致し、他社の
    ドメインを採ったものは無かった。WHOIS より多く拾った2社（社名変更・
    事業子会社）も正しかった
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

#: 推測に使わない語。法人格と、どの会社にも付く語
_STOP = {
    "CO",
    "LTD",
    "LIMITED",
    "INC",
    "CORP",
    "CORPORATION",
    "COMPANY",
    "KABUSHIKI",
    "KAISHA",
    "KK",
    "K",
    "HOLDINGS",
    "HOLDING",
    "GROUP",
    "THE",
    "AND",
    "OF",
}

#: 日本語の商号から外す語。残った部分がページに載っているかを見る
_JA_DROP = ("株式会社", "ホールディングス", "グループ", "（株）", "(株)", "・", " ", "　")

#: 社名の芯がこれより短いと、他社のページにも偶然載る
MIN_CORE_CHARS = 3

#: 1社あたりに試す推測の数の上限
MAX_GUESSES = 4

#: 読むページの大きさ
MAX_BYTES = 2_000_000


def name_tokens(name_en: str | None) -> list[str]:
    """英字社名から、法人格などを除いた語を小文字で返す。"""
    words = re.sub(r"[^A-Z0-9 ]", " ", (name_en or "").upper()).split()
    return [w.lower() for w in words if w not in _STOP]


def guesses(name_en: str | None) -> list[str]:
    """推測する .co.jp の候補。**つなげた形 → ハイフン → 先頭の語** の順。

    先頭の語だけの形は他社のものであることが多いが、候補には入れる
    （確かめはページで行う）。3文字未満の語は使わない。
    """
    words = name_tokens(name_en)
    if not words:
        return []
    forms = ["".join(words), "-".join(words), words[0]]
    if len(words) >= 2:
        forms.append(words[0] + words[1])
    out = list(dict.fromkeys(f"{f}.co.jp" for f in forms if len(f) >= 3))
    return out[:MAX_GUESSES]


def name_core(name_ja: str | None) -> str:
    """日本語の商号から、ページで探す芯を取り出す（NFKC 済み）。"""
    text = unicodedata.normalize("NFKC", name_ja or "")
    for word in _JA_DROP:
        text = text.replace(unicodedata.normalize("NFKC", word), "")
    return text


def page_names_company(page: str, core: str) -> bool:
    """ページに社名の芯が載っているか。空白の違いは無視する。"""
    if len(core) < MIN_CORE_CHARS:
        return False
    text = unicodedata.normalize("NFKC", page or "")
    text = re.sub(r"\s+", "", text).replace("・", "")
    return core in text


@dataclass
class GuessStats:
    attempted: int = 0
    no_english_name: int = 0
    #: 社名の芯が短すぎて、ページで確かめられない
    core_too_short: int = 0
    #: 推測が1つも DNS に無かった
    no_guess_exists: int = 0
    #: 実在したが、ページを1つも読めなかった。**「違った」ではない**（原則5）
    page_unreachable: int = 0
    #: 読めたが、社名が載っていなかった（他社のもの、または別の構成）
    name_not_on_page: int = 0
    found: int = 0
    dns_queries: int = 0
    pages_fetched: int = 0
    sample_found: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "no_english_name": self.no_english_name,
            "core_too_short": self.core_too_short,
            "no_guess_exists": self.no_guess_exists,
            "page_unreachable": self.page_unreachable,
            "name_not_on_page": self.name_not_on_page,
            "found": self.found,
            "dns_queries": self.dns_queries,
            "pages_fetched": self.pages_fetched,
            "sample_found": self.sample_found[:5],
        }


class CoJpGuesser:
    """推測して、DNS で実在を見て、ページで確かめる。

    `exists(name)` と `fetch(url)` を差し替えられる（検査はネットワークに出ない）。
    `fetch` は本文を返す。読めなければ None。
    """

    def __init__(
        self,
        exists: Callable[[str], bool],
        fetch: Callable[[str], str | None],
    ) -> None:
        self.exists = exists
        self.fetch = fetch
        self.stats = GuessStats()

    def domain_for(self, name_ja: str | None, name_en: str | None) -> str | None:
        self.stats.attempted += 1
        candidates = guesses(name_en)
        if not candidates:
            self.stats.no_english_name += 1
            return None
        core = name_core(name_ja)
        if len(core) < MIN_CORE_CHARS:
            self.stats.core_too_short += 1
            return None

        existing = []
        for domain in candidates:
            self.stats.dns_queries += 1
            if self.exists(domain):
                existing.append(domain)
        if not existing:
            self.stats.no_guess_exists += 1
            return None

        read_any = False
        for domain in existing:
            for url in (f"https://www.{domain}/", f"https://{domain}/"):
                self.stats.pages_fetched += 1
                page = self.fetch(url)
                if page is None:
                    continue
                read_any = True
                if page_names_company(page, core):
                    self.stats.found += 1
                    if len(self.stats.sample_found) < 5:
                        self.stats.sample_found.append(domain)
                    return domain
                # www で読めて載っていなければ、裸のドメインは見ない
                break
        if read_any:
            self.stats.name_not_on_page += 1
        else:
            self.stats.page_unreachable += 1
        return None
