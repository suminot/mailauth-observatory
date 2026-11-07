"""株価指数の構成銘柄を、見るための母集団として付ける（日経平均株価など）。

**計測は jp-all-listed の1本だけ回し、P7 で構成銘柄に印を付けて別に集計する。**
母集団を変えて計測し直すと、月次の比較ができなくなるため（views と同じ考え）。

一覧の出どころ
  日経平均株価の構成銘柄は、**日本語版 Wikipedia の記事「日経平均株価」の
  「構成銘柄一覧」節**（CC BY-SA 4.0）から証券コードだけを取る。

  日経の指数サイト（indexes.nikkei.co.jp）は使わない。利用規約
  （2019-08-30 更新）が「本サービスで提供される情報を蓄積すること」と
  第三者への提供を書面の許可なく禁じており、自動での取得も 403 で拒む。
  構成銘柄であるという事実と証券コードは、記事の出典表示とともに使う。
  日経平均株価の著作権は日本経済新聞社に帰属する（サイトに表示する）。
"""

from __future__ import annotations

import csv
import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..normalize import normalize_securities_code

DERIVED_CONFIG = Path("configs/derived_populations.yaml")
WIKIPEDIA_RAW = "https://ja.wikipedia.org/w/index.php?title={title}&action=raw"

_CODE_ROW = re.compile(r"^\|\s*([0-9][0-9A-Z]{3})\s*(?:\|\||$)", re.M)


def parse_wikipedia_constituents(wikitext: str, section: str = "構成銘柄一覧") -> list[str]:
    """記事の「構成銘柄一覧」節の表から証券コード（4桁、英字を含むものも）を出現順に。"""
    start = wikitext.find(f"== {section} ==")
    if start < 0:
        return []
    body = wikitext[start + len(section) + 6:]
    end = re.search(r"\n==[^=]", body)
    if end:
        body = body[: end.start()]
    out: list[str] = []
    for code in _CODE_ROW.findall(body):
        if code not in out:
            out.append(code)
    return out


@dataclass
class DerivedPopulation:
    id: str
    label: str
    parent: str
    member_list: str
    expected_size: int | None = None
    source: dict = field(default_factory=dict)
    #: 一覧の証券コードと突き合わせる列。グループ会社は親の証券コード
    #: （`parent_securities_code`）で突き合わせる
    match: str = "securities_code"

    def codes(self) -> set[str]:
        path = Path(self.member_list)
        if not path.exists():
            return set()
        with path.open(encoding="utf-8") as f:
            rows = csv.DictReader(f)
            codes = (normalize_securities_code(r.get("securities_code")) for r in rows)
            return {c for c in codes if c}


def load_derived(path: Path = DERIVED_CONFIG) -> list[DerivedPopulation]:
    if not Path(path).exists():
        return []
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return [
        DerivedPopulation(
            id=str(d["id"]), label=str(d.get("label") or d["id"]), parent=str(d["parent"]),
            member_list=str(d["member_list"]), expected_size=d.get("expected_size"),
            source=d.get("source") or {},
            match=str(d.get("match") or "securities_code"),
        )
        for d in data.get("populations") or []
    ]


def write_member_list(path: Path, codes: list[str], *, retrieved: dt.date) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["securities_code", "retrieved"])
        for c in codes:
            w.writerow([c, retrieved.isoformat()])
