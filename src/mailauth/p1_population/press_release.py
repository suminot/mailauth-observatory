"""決算発表などのプレスリリースから公式サイトを取る（BACKLOG 15）。

年次報告書（`annual_report.py`）で取れなかった米国企業向け。2026-10-21 の
全数実行で、年次報告書のある事業会社のうち 629 社が自社サイトを書いて
いなかった。

プレスリリース（8-K / 6-K の添付 EX-99）には、末尾にほぼ必ず
「**About 〇〇** … www.example.com」の会社紹介と、IR の問い合わせ先がある。

守っていること
  - **社名と合うドメインだけを採る**（`name_matches_domain`）。プレスリリースの
    問い合わせ先は IR 代行会社やコンサル会社のことが多い。2026-10-21 の
    標本40社では、条件なしだと `lifesciadvisors.com` などが混ざった。
    条件を付けると 17 社が取れて、目で見た範囲で誤りは無かった
  - **メールアドレスは保存しない。** `@` より後ろのドメインだけをその場で
    取り出す。問い合わせ先は個人名入りのアドレスが多い（DMARC の rua と同じ扱い）
  - 結果は受付番号で持ち越す。提出済みの書類は変わらない
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .annual_report import THIRD_PARTY, _candidate, _plain_text, name_matches_domain

#: 読む様式。米国企業は 8-K、外国企業は 6-K
RELEASE_FORMS = ("8-K", "6-K")

#: 1社あたり読むプレスリリースの数（新しい順）
MAX_RELEASES = 3

#: 添付は短い。全部読んでも 1 MB に届かないことがほとんど
MAX_BYTES = 1_000_000

#: 読み方を変えたら上げる。版が違うキャッシュは読み直す
EXTRACTOR_VERSION = 1

INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/index.json"
FILE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{name}"

#: 「About 〇〇」の見出し。**大文字始まりの固有名詞が続くときだけ**
#: （「about the quarter」を拾わない）
_ABOUT = re.compile(r"\bAbout\s+[A-Z]")

#: 見出しの後ろでドメインを探す範囲（文字数）。会社紹介は数文で終わる
_ABOUT_WINDOW = 900

#: メールアドレスのドメイン部だけを取り出す。**ローカル部は捕まえない**
_EMAIL_DOMAIN = re.compile(r"[A-Za-z0-9._%+-]+@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})")

_HOST = re.compile(
    r"(?<![@\w.-])(?:https?://)?(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}"
    r"(?![\w-])",
    re.I,
)

#: プレスリリースに出てくる、会社のものではないドメイン
RELEASE_THIRD_PARTY = THIRD_PARTY | frozenset(
    {
        "accesswire.com",
        "newsfilecorp.com",
        "einpresswire.com",
        "doi.org",
        # 個人向けのメール
        "gmail.com",
        "yahoo.com",
        "outlook.com",
        "hotmail.com",
        "163.com",
        "126.com",
        "qq.com",
    }
)

#: 添付の名前。`ex99-1.htm` / `ex_99.htm` / `d123dex991.htm` など
_EXHIBIT = re.compile(r"ex[-_]?99", re.I)


def recent_releases(submissions: dict[str, Any] | None) -> list[str]:
    """submissions から、新しい順に 8-K / 6-K の受付番号を返す。"""
    recent = ((submissions or {}).get("filings") or {}).get("recent") or {}
    out: list[str] = []
    for form, accession in zip(
        recent.get("form") or [], recent.get("accessionNumber") or [], strict=False
    ):
        if form in RELEASE_FORMS and accession:
            out.append(accession)
            if len(out) >= MAX_RELEASES:
                break
    return out


def release_domains(raw: str) -> Counter[str]:
    """プレスリリースの本文から、会社のものかもしれないドメインを数える。

    「About 〇〇」の直後の最初のドメインと、メールアドレスのドメイン部。
    **どちらも候補でしかない。** 採るかどうかは社名との突き合わせで決める。
    """
    text = _plain_text(raw)
    found: Counter[str] = Counter()
    for heading in _ABOUT.finditer(text):
        window = text[heading.start() : heading.start() + _ABOUT_WINDOW]
        for m in _HOST.finditer(window):
            cand = _candidate(m.group(0))
            if not cand:
                continue
            _, domain = cand
            if domain in RELEASE_THIRD_PARTY or domain.endswith(".gov"):
                continue
            found[domain] += 1
            break
    for m in _EMAIL_DOMAIN.finditer(text):
        cand = _candidate(m.group(1))
        if not cand:
            continue
        _, domain = cand
        if domain in RELEASE_THIRD_PARTY or domain.endswith(".gov"):
            continue
        found[domain] += 1
    return found


@dataclass
class PressReleaseStats:
    #: プレスリリースを読もうとした企業（年次報告書でも埋まらなかった企業）
    attempted: int = 0
    #: 8-K / 6-K が1件も無かった
    no_releases: int = 0
    fetched: int = 0
    cache_hit: int = 0
    fetch_failed: int = 0
    found: int = 0
    #: ドメインはあったが、社名と合うものが無かった（IR 代行など）
    name_mismatch: int = 0
    #: 読めたが、ドメインが1つも無かった
    nothing_found: int = 0
    #: 1件も読めなかった。**「無かった」ではなく「読めなかった」**（原則5）
    unreadable: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "no_releases": self.no_releases,
            "fetched": self.fetched,
            "cache_hit": self.cache_hit,
            "fetch_failed": self.fetch_failed,
            "found": self.found,
            "name_mismatch": self.name_mismatch,
            "nothing_found": self.nothing_found,
            "unreadable": self.unreadable,
            "notes": self.notes[:5],
        }


class PressReleaseWebsites:
    """プレスリリースから公式サイトを取る。結果は受付番号で持ち越す。

    取得は `SecEdgarClient` に任せる（User-Agent と 10 req/s 未満の作法）。
    """

    def __init__(self, client, *, cache_dir: Path | None = None) -> None:
        self.client = client
        self.cache_dir = cache_dir or (client.cache_dir / "press_release")
        self.stats = PressReleaseStats()

    def _domains_for(self, cik: str, accession: str) -> Counter[str] | None:
        """1件のプレスリリースのドメイン。読めなければ None（持ち越さない）。"""
        path = self.cache_dir / f"{accession}.json"
        if path.is_file():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cached = None
            if cached is not None and cached.get("extractor_version") == EXTRACTOR_VERSION:
                self.stats.cache_hit += 1
                return Counter(cached.get("domains") or {})

        folder = accession.replace("-", "")
        try:
            index = self.client.get_json(
                INDEX_URL.format(cik=int(cik), accession=folder)
            ) or {}
            names = [
                item.get("name") or ""
                for item in (index.get("directory") or {}).get("item") or []
            ]
            exhibits = [
                n
                for n in names
                if _EXHIBIT.search(n) and n.lower().endswith((".htm", ".html", ".txt"))
            ]
            raw = ""
            if exhibits:
                raw = self.client.get_text(
                    FILE_URL.format(cik=int(cik), accession=folder, name=exhibits[0]),
                    max_bytes=MAX_BYTES,
                ) or ""
        except Exception as exc:  # noqa: BLE001 - 1社の失敗で母集団を止めない
            self.stats.fetch_failed += 1
            if len(self.stats.notes) < 10:
                self.stats.notes.append(f"{accession}: {exc}")
            return None

        self.stats.fetched += 1
        domains = release_domains(raw) if raw else Counter()
        path.parent.mkdir(parents=True, exist_ok=True)
        # **ドメインと回数だけを書く。** メールアドレスはどこにも残さない
        path.write_text(
            json.dumps(
                {"cik": cik, "domains": dict(domains), "extractor_version": EXTRACTOR_VERSION},
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return domains

    def website_for(self, cik: str | None, accessions: list[str], name: str | None) -> str | None:
        self.stats.attempted += 1
        if not cik or not accessions:
            self.stats.no_releases += 1
            return None
        total: Counter[str] = Counter()
        read = 0
        for accession in accessions:
            domains = self._domains_for(cik, accession)
            if domains is None:
                continue
            read += 1
            total.update(domains)
        if not total:
            if read:
                self.stats.nothing_found += 1
            else:
                self.stats.unreadable += 1
            return None
        matched = [(d, n) for d, n in total.most_common() if name_matches_domain(name, d)]
        if not matched:
            self.stats.name_mismatch += 1
            return None
        self.stats.found += 1
        return f"https://{matched[0][0]}"
