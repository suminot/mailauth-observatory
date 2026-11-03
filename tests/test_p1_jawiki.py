"""日本語版ウィキペディアの基礎情報から公式サイトを取る（BACKLOG 28）。

**同じ会社の記事であることは、市場情報の証券コードで確かめる。** 検査の大半は
「別の会社のサイトを付けない」ことを見ている。

テストは一切ネットワークに出ない。取得層は差し替える。
"""

from __future__ import annotations

import pytest
import yaml

from mailauth.p1_population.jawiki import (
    JawikiWebsites,
    candidate_titles,
    code_listed,
    external_link,
)


def _article(code: str, link: str, *, extra: str = "") -> str:
    return (
        "'''株式会社テスト'''は…\n"
        "{{基礎情報 会社\n"
        "| 社名 = 株式会社テスト\n"
        f"| 市場情報 = {{{{上場情報 | 東証スタンダード | {code} | 2001年1月}}}}\n"
        "| 本社所在地 = [[東京都]][[千代田区]]\n"
        f"{extra}"
        f"| 外部リンク = {link}\n"
        "}}\n本文"
    )


def _infobox(text: str) -> str:
    from mailauth.p1_population.jawiki import _infobox

    box = _infobox(text)
    assert box is not None
    return box


def test_記事の名前は法人格を落としたものと_企業_を付けたもの():
    assert candidate_titles("株式会社秋川牧園") == ["秋川牧園", "秋川牧園 (企業)"]
    assert candidate_titles("徳倉建設株式会社") == ["徳倉建設", "徳倉建設 (企業)"]
    assert candidate_titles("") == []


def test_全角の英数字は半角にした名前も試す():
    assert candidate_titles("Ｕｍｉｏｓ株式会社") == [
        "Ｕｍｉｏｓ", "Ｕｍｉｏｓ (企業)", "Umios", "Umios (企業)",
    ]


def test_市場情報の証券コードで同じ会社かを確かめる():
    box = _infobox(_article("1380", "https://www.example.co.jp/"))
    assert code_listed(box, "1380")
    assert not code_listed(box, "1381")


def test_資本金などの数字に紛れた4桁では同じ会社と見なさない():
    """市場情報の欄だけを見る。"""
    box = _infobox(
        _article("9999", "https://www.example.co.jp/", extra="| 資本金 = 1380万円\n")
    )
    assert not code_listed(box, "1380")


@pytest.mark.parametrize(
    "link, expected",
    [
        ("https://www.example.co.jp/", "https://www.example.co.jp/"),
        ("[https://www.example.co.jp/ 公式サイト]", "https://www.example.co.jp/"),
        ("{{URL|www.example.co.jp}}", "https://www.example.co.jp"),
        ("{{Official URL}}", None),
        ("", None),
    ],
)
def test_外部リンクの書き方(link, expected):
    assert external_link(_infobox(_article("1380", link))) == expected


def _reader(pages: dict[str, str | None]) -> JawikiWebsites:
    def fetch(titles):
        return {t: pages.get(t) for t in titles}

    return JawikiWebsites(fetch)


def test_コードが合う記事だけから取る():
    pages = {
        "秋川牧園": _article("1380", "https://www.akikawa-foods.co.jp/"),
        # 同名の別会社の記事（コードが違う）
        "徳倉建設": _article("9999", "https://www.wrong.co.jp/"),
    }
    reader = _reader(pages)
    got = reader.websites_for(
        [("jp:1", "株式会社秋川牧園", "13800"), ("jp:2", "徳倉建設株式会社", "18350"),
         ("jp:3", "株式会社記事なし", "12340")],
        taken=set(),
    )
    assert got == {"jp:1": "https://www.akikawa-foods.co.jp/"}
    s = reader.stats
    assert (s.found, s.code_mismatch, s.no_article) == (1, 1, 1)


def test_基礎情報の無い記事からは取らない():
    reader = _reader({"テスト": "テストは曖昧さ回避のページです。"})
    assert reader.websites_for([("jp:1", "株式会社テスト", "13800")], taken=set()) == {}
    assert reader.stats.no_company_infobox == 1


def test_会社以外の基礎情報は数えて残す():
    bank = "{{基礎情報 銀行\n| 銀行名 = テスト銀行\n| 外部リンク = https://www.test-bank.co.jp/\n}}"
    reader = _reader({"テスト銀行": bank})
    assert reader.websites_for([("jp:1", "株式会社テスト銀行", "83000")], taken=set()) == {}
    assert reader.stats.to_dict()["other_infoboxes"] == {"基礎情報 銀行": 1}


def test_他社に付いているドメインと_SNS_は採らない():
    pages = {
        "子会社": _article("1111", "https://www.parent.co.jp/"),
        "SNS社": _article("2222", "https://twitter.com/sns_co"),
    }
    reader = _reader(pages)
    got = reader.websites_for(
        [("jp:1", "株式会社子会社", "11110"), ("jp:2", "株式会社SNS社", "22220")],
        taken={"parent.co.jp"},
    )
    assert got == {}
    assert (reader.stats.taken_by_other, reader.stats.not_company_domain) == (1, 1)


def test_この中で2社に同じドメインが出たらどちらにも付けない():
    pages = {
        "甲": _article("1111", "https://www.group.co.jp/"),
        "乙": _article("2222", "https://www.group.co.jp/"),
    }
    reader = _reader(pages)
    got = reader.websites_for(
        [("jp:1", "株式会社甲", "11110"), ("jp:2", "株式会社乙", "22220")], taken=set()
    )
    assert got == {}
    assert reader.stats.taken_by_other == 2


def test_引けなかったのと記事が無いのを分ける():
    def fetch(titles):
        raise RuntimeError("down")

    reader = JawikiWebsites(fetch)
    assert reader.websites_for([("jp:1", "株式会社テスト", "13800")], taken=set()) == {}
    assert (reader.stats.fetch_failed, reader.stats.no_article) == (1, 0)


def _entity(eid: str, name: str, code: str | None, domain: str | None = None):
    from mailauth.contracts import Entity

    return Entity(
        entity_id=eid, run_id="2026-11", country="JP", population_ids=["jp-all-listed"],
        name=name, name_normalized=name, securities_code=code, official_domain=domain,
    )


def test_空欄の会社だけを埋め_既にある値は上書きしない():
    from mailauth.manifest import RunManifest
    from mailauth.p1_population.runner import _fill_from_jawiki

    have = _entity("jp:1", "株式会社既存", "11110", "kizon.co.jp")
    empty = _entity("jp:2", "株式会社秋川牧園", "13800")
    pages = {
        "既存": _article("1111", "https://www.other.co.jp/"),
        "秋川牧園": _article("1380", "https://www.akikawa-foods.co.jp/"),
    }
    manifest = RunManifest(run_id="2026-11", phase="p1_population", out_dir="/tmp/unused")
    _fill_from_jawiki([have, empty], manifest,
                      fetch=lambda ts: {t: pages.get(t) for t in ts})
    assert have.official_domain == "kizon.co.jp"
    assert empty.official_domain == "akikawa-foods.co.jp"
    assert empty.official_url == "https://www.akikawa-foods.co.jp/"
    b = manifest.breakdown["jawiki_infobox"]
    assert (b["missing_before"], b["found"], b["still_missing"]) == (1, 1, 0)


def test_offline_では引かず_そう書く():
    from mailauth.manifest import RunManifest
    from mailauth.p1_population.runner import _fill_from_jawiki

    e = _entity("jp:2", "株式会社秋川牧園", "13800")
    manifest = RunManifest(run_id="2026-11", phase="p1_population", out_dir="/tmp/unused")
    _fill_from_jawiki([e], manifest, offline=True)
    assert e.official_domain is None
    assert "ENRICH_SKIPPED_JAWIKI" in {w.code for w in manifest.warnings}


def test_国内の母集団は推測のあとにこの経路を使う():
    data = yaml.safe_load(open("configs/populations/jp-all-listed.yaml", encoding="utf-8"))
    enrich = data["source"]["enrich"]
    assert enrich.index("co_jp_homepage") < enrich.index("jawiki_infobox")
    assert any("ウィキペディア" in a for a in data["attribution"])
