"""グループ会社の母集団（jp-all-listed-group、BACKLOG 52 の三歩目）。"""

from __future__ import annotations

import csv

import pandas as pd

from mailauth.io import read_parquet
from mailauth.p1_population import edinet_koukoku, group_domains
from mailauth.p1_population.edinet import EdinetRow
from mailauth.p1_population.group_population import build_group_entities
from mailauth.p1_population.runner import run as run_p1
from mailauth.paths import phase_output

CONFIG = "configs/populations/jp-all-listed-group.yaml"
RUN = "2026-11"


def _row(code, name, sec, hb, status="上場"):
    return EdinetRow(edinet_code=code, submitter_type="内国法人・組合", listing_status=status,
                     name=name, name_en="", address="", industry="電気機器",
                     securities_code=sec, houjin_bangou=hb)


PARENTS = [
    _row("E00001", "親電機株式会社", "66660", "1000000000001"),
    _row("E00002", "上場子会社株式会社", "77770", "1000000000002"),
    _row("E00003", "やめた株式会社", "88880", "1000000000003", status="非上場"),
]

GROUP = {
    ("E00001", "子電機サービス株式会社"): ("2000000000001", "kodenki-s.co.jp", "found"),
    # 上場子会社。jp-all-listed で測っている
    ("E00001", "上場子会社株式会社"): ("1000000000002", "joujou.co.jp", "found"),
    # 親会社のサイトの一部をそのまま会社のページにしている
    ("E00001", "親電機ソリューションズ株式会社"): ("2000000000002", "oyadenki.co.jp", "found"),
    # 合弁：同じ法人番号が2つの親に出る。EDINET コードの若い親に付ける
    ("E00002", "子電機サービス株式会社"): ("2000000000001", "kodenki-s.co.jp", "found"),
    ("E00001", "サイトの無い株式会社"): ("2000000000003", "", "no_url"),
    ("E00001", "同名株式会社"): ("", "", "ambiguous"),
}


def test_子会社を別の母集団の会社にする():
    entities, stats = build_group_entities(
        GROUP, [p for p in PARENTS if p.listing_status == "上場"], {"oyadenki.co.jp"},
        population_id="jp-all-listed-group", run_id=RUN,
    )
    assert [(e.entity_id, e.official_domain, e.parent_entity_id, e.parent_securities_code)
            for e in entities] == [
        ("jp:2000000000001", "kodenki-s.co.jp", "jp:1000000000001", "6666"),
    ]
    e = entities[0]
    assert e.population_ids == ["jp-all-listed-group"]
    assert e.name == "子電機サービス株式会社"
    assert e.securities_code is None
    assert stats["listed_company"] == 1
    assert stats["listed_domain"] == 1
    assert stats["duplicate_number"] == 1
    assert stats["found"] == 4


def test_親が上場をやめた子会社は作らない():
    group = {("E00003", "孫株式会社"): ("2000000000009", "mago.co.jp", "found")}
    entities, stats = build_group_entities(
        group, [p for p in PARENTS if p.listing_status == "上場"], set(),
        population_id="jp-all-listed-group", run_id=RUN,
    )
    assert entities == []
    assert stats["parent_not_listed"] == 1


def _write_group_csv(path, data):
    group_domains.write_csv(path, data)


def test_P1_を回すと親の付いた子会社が出る(monkeypatch, tmp_path):
    gpath = tmp_path / "subsidiary_domains.csv"
    _write_group_csv(gpath, GROUP)
    kpath = tmp_path / "koukoku_domains.csv"
    with kpath.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["edinet_code", "doc_id", "domain", "scanned_on"])
        w.writerow(["E00001", "S1", "oyadenki.co.jp", "2026-11-06"])
    monkeypatch.setattr(group_domains, "GROUP_CSV", str(gpath))
    monkeypatch.setattr(edinet_koukoku, "SCAN_CSV", str(kpath))

    result = run_p1(config=CONFIG, run_id=RUN, offline=True, rows=PARENTS)

    df = read_parquet(phase_output(RUN, "p1_population", "entities.parquet"))
    assert isinstance(df, pd.DataFrame)
    assert list(df["entity_id"]) == ["jp:2000000000001"]
    assert list(df["parent_entity_id"]) == ["jp:1000000000001"]
    assert result["breakdown"]["group"]["listed_domain"] == 1
    assert result["breakdown"]["parents_with_group"] == 1


def test_一覧が無ければ黙らない(monkeypatch, tmp_path):
    monkeypatch.setattr(group_domains, "GROUP_CSV", str(tmp_path / "none.csv"))
    monkeypatch.setattr(edinet_koukoku, "SCAN_CSV", str(tmp_path / "none2.csv"))
    result = run_p1(config=CONFIG, run_id=RUN, offline=True, rows=PARENTS)
    assert "GROUP_LIST_EMPTY" in {w["code"] for w in result["warnings"]}
    assert result["counts"]["success"] == 0


def test_gBizINFOにサイトが無い法人は法人番号でWikidataのサイトを使う():
    group = {("E00001", "子電機ロジ株式会社"): ("2000000000005", "", "no_url"),
             ("E00001", "子電機サイト無し株式会社"): ("2000000000006", "", "no_url")}
    entities, stats = build_group_entities(
        group, [p for p in PARENTS if p.listing_status == "上場"], set(),
        population_id="jp-all-listed-group", run_id=RUN,
        sites={"2000000000005": "https://www.kodenki-logi.co.jp/"},
    )
    assert [(e.entity_id, e.official_domain) for e in entities] == [
        ("jp:2000000000005", "kodenki-logi.co.jp"),
    ]
    assert stats["from_wikidata"] == 1


def test_3社以上の親の下に出る法人は子会社として扱わない():
    """注記に出てくる借入先・信託口の銀行（2026-11-07：日本カストディ銀行が 27 社の下に出た）。"""
    parents = [_row(f"E0000{i}", f"親{i}株式会社", f"{i}0000", f"100000000000{i}")
               for i in (1, 2, 3)]
    group = {(f"E0000{i}", "株式会社信託口銀行"): ("3000000000001", "trust.example.jp", "found")
             for i in (1, 2, 3)}
    group[("E00001", "子株式会社")] = ("3000000000002", "ko.example.jp", "found")
    group[("E00002", "子株式会社")] = ("3000000000002", "ko.example.jp", "found")
    entities, stats = build_group_entities(
        group, parents, set(), population_id="jp-all-listed-group", run_id=RUN,
    )
    assert [e.entity_id for e in entities] == ["jp:3000000000002"]  # 合弁（親2社）は残す
    assert stats["many_parents"] == 3


def test_同じ親の子会社が3社以上で使うドメインはグループ共通サイトとして外す():
    group = {("E00001", f"子{i}株式会社"): (f"400000000000{i}", "group.example.com", "found")
             for i in (1, 2, 3)}
    group[("E00001", "子4株式会社")] = ("4000000000004", "ko4.example.jp", "found")
    entities, stats = build_group_entities(
        group, [p for p in PARENTS if p.listing_status == "上場"], set(),
        population_id="jp-all-listed-group", run_id=RUN,
    )
    assert [e.official_domain for e in entities] == ["ko4.example.jp"]
    assert stats["group_site"] == 3


# ---- サイトの登録が無い子会社の .co.jp の推測（group_guess） ----

from mailauth.p1_population.co_jp_guess import CoJpGuesser  # noqa: E402
from mailauth.p1_population.group_guess import (  # noqa: E402
    guess_subsidiaries,
    guess_targets,
    parent_marks,
)


def _guesser(pages: dict[str, str]):
    hosts = {u.split("/")[2].removeprefix("www.") for u in pages}
    return CoJpGuesser(exists=lambda d: d in hosts, fetch=lambda u: pages.get(u))


MARKS = {"E00001": parent_marks("親電機株式会社", "OYADENKI CORPORATION", ["oyadenki.co.jp"])}


def test_読みから推測して商号と親会社が載っていれば採る():
    g = _guesser({"https://www.kodenkiroji.co.jp/":
                  "<title>子電機ロジ株式会社</title><p>親電機グループの物流会社です</p>"})
    found, stats = guess_subsidiaries(
        {"2000000000005": ("子電機ロジ株式会社", "E00001")},
        {"2000000000005": {"kana": "コデンキロジ"}}, MARKS, g,
    )
    assert found == {"2000000000005": "kodenkiroji.co.jp"}
    assert stats.found == 1


def test_親会社が載っていなければ同じ商号の別会社かもしれないので採らない():
    g = _guesser({"https://www.sanko.co.jp/": "<title>株式会社サンコー</title><p>創業 1950 年</p>"})
    found, stats = guess_subsidiaries(
        {"2000000000007": ("株式会社サンコー", "E00001")},
        {"2000000000007": {"kana": "サンコー"}}, MARKS, g,
    )
    assert found == {}
    assert stats.no_parent_mention == 1
    assert stats.sample_no_parent_mention == ["sanko.co.jp ← 株式会社サンコー"]


def test_子会社の商号の中に出る親の名前は親会社に触れたことにしない():
    g = _guesser({"https://www.oyadenkisabisu.co.jp/":
                  "<title>親電機サービス株式会社</title><p>親電機サービスへようこそ</p>"})
    found, stats = guess_subsidiaries(
        {"2000000000008": ("親電機サービス株式会社", "E00001")},
        {"2000000000008": {"kana": "オヤデンキサービス"}}, MARKS, g,
    )
    assert found == {}
    assert stats.no_parent_mention == 1


def test_親会社のドメインへのリンクでもよい():
    g = _guesser({"https://www.oyadenkisabisu.co.jp/":
                  '<title>親電機サービス株式会社</title><a href="https://www.oyadenki.co.jp/">'})
    found, _ = guess_subsidiaries(
        {"2000000000008": ("親電機サービス株式会社", "E00001")},
        {"2000000000008": {"kana": "オヤデンキサービス"}}, MARKS, g,
    )
    assert found == {"2000000000008": "oyadenkisabisu.co.jp"}


def test_読みが無い子会社と持ち時間を過ぎた子会社は推測しない():
    g = _guesser({})
    targets = {"2000000000005": ("子電機ロジ株式会社", "E00001"),
               "2000000000006": ("子電機テック株式会社", "E00001")}
    _, stats = guess_subsidiaries(targets, {"2000000000006": {"kana": "コデンキテック"}},
                                  MARKS, g, deadline=0.0, clock=lambda: 1.0)
    assert stats.no_reading == 1
    assert stats.deferred == 1
    assert stats.attempted == 0


def test_推測の結果は親会社の印も鍵にして持ち越す():
    store: dict = {}

    class Cache:
        def get(self, args):
            return store.get(args)

        def put(self, args, domain, kind):
            store[args] = (domain, kind)

    pages = {"https://www.kodenkiroji.co.jp/":
             "<title>子電機ロジ株式会社</title><p>親電機グループ</p>"}
    args = ({"2000000000005": ("子電機ロジ株式会社", "E00001")},
            {"2000000000005": {"kana": "コデンキロジ"}}, MARKS)
    guess_subsidiaries(*args, _guesser(pages), cache=Cache())
    found, stats = guess_subsidiaries(*args, _guesser({}), cache=Cache())
    assert found == {"2000000000005": "kodenkiroji.co.jp"}
    assert stats.cache_hit == 1
    assert all("parent=" in k[-1] for k in store)


def test_推測するのはサイトの無い子会社のうち作られうるものだけ():
    group = dict(GROUP)
    group[("E00001", "子電機ロジ株式会社")] = ("2000000000005", "", "no_url")
    group[("E00003", "孫株式会社")] = ("2000000000009", "", "no_url")  # 親が上場をやめた
    group[("E00001", "子電機サイト有り株式会社")] = ("2000000000006", "", "no_url")
    targets = guess_targets(group, ["E00001", "E00002"], {"1000000000001", "1000000000002"},
                            {"2000000000006": "https://example.jp/"})
    assert targets == {
        "2000000000003": ("サイトの無い株式会社", "E00001"),
        "2000000000005": ("子電機ロジ株式会社", "E00001"),
    }


def test_推測したドメインで子会社を作る():
    group = {("E00001", "子電機ロジ株式会社"): ("2000000000005", "", "no_url")}
    entities, stats = build_group_entities(
        group, [p for p in PARENTS if p.listing_status == "上場"], set(),
        population_id="jp-all-listed-group", run_id=RUN,
        guessed={"2000000000005": "kodenkiroji.co.jp"},
    )
    assert [(e.entity_id, e.official_domain) for e in entities] == [
        ("jp:2000000000005", "kodenkiroji.co.jp"),
    ]
    assert stats["from_co_jp_guess"] == 1


def test_offlineでは推測していないと残す(monkeypatch, tmp_path):
    gpath = tmp_path / "subsidiary_domains.csv"
    _write_group_csv(gpath, GROUP)
    monkeypatch.setattr(group_domains, "GROUP_CSV", str(gpath))
    monkeypatch.setattr(edinet_koukoku, "SCAN_CSV", str(tmp_path / "none.csv"))
    result = run_p1(config=CONFIG, run_id=RUN, offline=True, rows=PARENTS)
    codes = {w["code"] for w in result["warnings"]}
    assert "ENRICH_SKIPPED_GROUP_CO_JP" in codes
    assert result["breakdown"]["group_co_jp_guess"] == {"targets": 1}
