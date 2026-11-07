"""株価指数の構成銘柄を、見るための母集団として付ける（日経平均株価）。

テストはネットワークに出ない。記事の本文は手で作る。
"""

from __future__ import annotations

import datetime as dt

from mailauth.p1_population.index_members import (
    DerivedPopulation,
    load_derived,
    parse_wikipedia_constituents,
    write_member_list,
)
from mailauth.p7_aggregate.runner import add_derived_populations

WIKI = """== 概要 ==
本文 1234 は数えない
== 構成銘柄一覧 ==
2026年10月1日現在。
=== 水産（1銘柄） ===
{|class="wikitable"
!証券コード!!銘柄!!備考
|-
|1332||[[ニッスイ]]||
|}
=== 建設 ===
{| class="wikitable"
|-
|1801
|[[大成建設]]
|
|-
|285A
|[[キオクシアホールディングス]]
|}
== 脚注 ==
|9999||載っていない
"""


def test_記事の構成銘柄一覧の節から証券コードだけを取る():
    assert parse_wikipedia_constituents(WIKI) == ["1332", "1801", "285A"]
    assert parse_wikipedia_constituents("節が無い") == []


def test_一覧の会社に派生の母集団の印を付ける(tmp_path):
    path = tmp_path / "list.csv"
    write_member_list(path, ["1332", "1801"], retrieved=dt.date(2026, 11, 7))
    d = DerivedPopulation("jp-nikkei225", "日経", "jp-all-listed", str(path), expected_size=2)
    ents = {
        "jp:1": {"population_ids": ["jp-all-listed"], "securities_code": "1332"},
        "jp:2": {"population_ids": ["jp-all-listed"], "securities_code": "7777"},
        # 親の母集団に入っていなければ付けない
        "us:1": {"population_ids": ["us-all-listed"], "securities_code": "1801"},
    }

    class M:
        warnings = []

        def add_warning(self, code, **kw):
            self.warnings.append(code)

    m = M()
    assert add_derived_populations(ents, [d], m) == {"jp-nikkei225": 1}
    assert ents["jp:1"]["population_ids"] == ["jp-all-listed", "jp-nikkei225"]
    assert ents["jp:2"]["population_ids"] == ["jp-all-listed"]
    assert ents["us:1"]["population_ids"] == ["us-all-listed"]
    # 見込み（2）と違うので警告
    assert m.warnings == ["DERIVED_POPULATION_SIZE"]


def test_置いてある日経平均の一覧は225件で出典を書いている():
    [d] = [x for x in load_derived() if x.id == "jp-nikkei225"]
    assert len(d.codes()) == 225
    assert d.source.get("license") == "CC BY-SA 4.0"
    assert "wikipedia.org" in d.source.get("url", "")
