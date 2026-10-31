"""英字社名から .co.jp を推測し、そのサイトに社名が載っているかで確かめる（BACKLOG 15）。

例はすべて 2026-10-31 の試験（国内の起点なし30社）で実際に出たもの。
テストは一切ネットワークに出ない。DNS とページは差し替える。
"""

from __future__ import annotations

from mailauth.p1_population.co_jp_guess import (
    CoJpGuesser,
    guesses,
    name_core,
    page_names_company,
)


def test_推測は_つなげた形_ハイフン_先頭の語の順():
    assert guesses("MURAKI CORPORATION") == ["muraki.co.jp"]
    assert guesses("Plus Alpha Consulting Co., Ltd") == [
        "plusalphaconsulting.co.jp",
        "plus-alpha-consulting.co.jp",
        "plus.co.jp",
        "plusalpha.co.jp",
    ]
    # 法人格とどの会社にも付く語は使わない
    assert guesses("Yokogawa Bridge Holdings Corp.") == [
        "yokogawabridge.co.jp",
        "yokogawa-bridge.co.jp",
        "yokogawa.co.jp",
    ]
    assert guesses("") == []
    assert guesses(None) == []


def test_社名の芯は法人格とホールディングスを除く():
    assert name_core("株式会社ムラキ") == "ムラキ"
    assert name_core("株式会社アイドマ・ホールディングス") == "アイドマ"
    # 全角英字は NFKC で半角に
    assert name_core("株式会社ＲＩＳＥ") == "RISE"


def test_ページの空白の違いは無視する():
    assert page_names_company("<title>株式会社 ムラキ｜会社案内</title>", "ムラキ")
    assert not page_names_company("<title>Area,Inc.</title>", "エリアクエスト")


class _Web:
    """DNS とページの差し替え。呼ばれた URL を覚える。"""

    def __init__(self, exists: set[str], pages: dict[str, str | None]):
        self._exists = exists
        self._pages = pages
        self.fetched: list[str] = []

    def exists(self, name: str) -> bool:
        return name in self._exists

    def fetch(self, url: str) -> str | None:
        self.fetched.append(url)
        return self._pages.get(url)


def test_サイトに社名が載っていれば採る():
    web = _Web({"muraki.co.jp"}, {"https://www.muraki.co.jp/": "<h1>ムラキ株式会社</h1>"})
    g = CoJpGuesser(web.exists, web.fetch)
    assert g.domain_for("ムラキ株式会社", "MURAKI CORPORATION") == "muraki.co.jp"
    assert g.stats.found == 1


def test_他社のドメインは採らない():
    """**短い語ほど他社が持っている。** area.co.jp は Area,Inc. のものだった。"""
    web = _Web(
        {"area.co.jp"},
        {"https://www.area.co.jp/": "<title>Area,Inc. 公式サイト</title>"},
    )
    g = CoJpGuesser(web.exists, web.fetch)
    assert g.domain_for("株式会社エリアクエスト", "Area Quest Inc.") is None
    assert g.stats.name_not_on_page == 1


def test_長い形を先に試し_見つかればそこで止める():
    web = _Web(
        {"yokogawa-bridge.co.jp", "yokogawa.co.jp"},
        {
            "https://www.yokogawa-bridge.co.jp/": "株式会社横河ブリッジホールディングス",
            "https://www.yokogawa.co.jp/": "横河電機株式会社",
        },
    )
    g = CoJpGuesser(web.exists, web.fetch)
    found = g.domain_for("株式会社横河ブリッジホールディングス", "Yokogawa Bridge Holdings Corp.")
    assert found == "yokogawa-bridge.co.jp"
    assert "https://www.yokogawa.co.jp/" not in web.fetched


def test_読めなかったのと載っていなかったのを分ける():
    """原則5。読めなかったものを「他社のもの」と数えない。"""
    web = _Web({"shinden.co.jp"}, {})
    g = CoJpGuesser(web.exists, web.fetch)
    assert g.domain_for("シンデン・ハイテックス株式会社", "Shinden Hightex Corporation") is None
    assert (g.stats.page_unreachable, g.stats.name_not_on_page) == (1, 0)
    # www が読めなければ裸のドメインも試している
    assert web.fetched == ["https://www.shinden.co.jp/", "https://shinden.co.jp/"]


def test_実在しない推測ではページを読まない():
    web = _Web(set(), {})
    g = CoJpGuesser(web.exists, web.fetch)
    assert g.domain_for("株式会社ＴＥＮＴＩＡＬ", "TENTIAL Inc.") is None
    assert g.stats.no_guess_exists == 1
    assert web.fetched == []


def test_社名の芯が短すぎるときは確かめない():
    """2文字の芯は他社のページにも偶然載る。"""
    web = _Web({"ab.co.jp"}, {"https://www.ab.co.jp/": "エイ"})
    g = CoJpGuesser(web.exists, web.fetch)
    assert g.domain_for("株式会社エイ", "A B Inc.") is None
    assert g.stats.core_too_short == 1
    assert web.fetched == []


def test_英字社名が無ければ推測しない():
    g = CoJpGuesser(lambda n: True, lambda u: "x")
    assert g.domain_for("株式会社テスト", "") is None
    assert g.stats.no_english_name == 1


# ---------------------------------------------------------------------------
# P1 に組み込まれていること
# ---------------------------------------------------------------------------


def test_空欄の企業だけを埋め_既にある値は上書きしない():
    from mailauth.contracts import Entity
    from mailauth.manifest import RunManifest
    from mailauth.p1_population.runner import _fill_from_co_jp_homepage

    have = Entity(
        entity_id="jp:1",
        run_id="2026-11",
        country="JP",
        population_ids=["jp-all-listed"],
        name="ムラキ株式会社",
        name_normalized="ムラキ",
        name_en="MURAKI CORPORATION",
        official_domain="muraki.jp",
    )
    empty = Entity(
        entity_id="jp:2",
        run_id="2026-11",
        country="JP",
        population_ids=["jp-all-listed"],
        name="株式会社タクミナ",
        name_normalized="タクミナ",
        name_en="tacmina corporation",
    )
    web = _Web(
        {"muraki.co.jp", "tacmina.co.jp"},
        {
            "https://www.muraki.co.jp/": "ムラキ",
            "https://www.tacmina.co.jp/": "株式会社タクミナ",
        },
    )
    manifest = RunManifest(run_id="2026-11", phase="p1_population", out_dir="/tmp/unused")
    _fill_from_co_jp_homepage([have, empty], manifest, guesser=CoJpGuesser(web.exists, web.fetch))
    assert have.official_domain == "muraki.jp", "既にある値を上書きした"
    assert empty.official_domain == "tacmina.co.jp"
    assert empty.official_url == "https://tacmina.co.jp/"
    b = manifest.breakdown["co_jp_homepage"]
    assert (b["missing_before"], b["filled"], b["still_missing"]) == (1, 1, 0)


def test_offline_では引かず_そう書く():
    from mailauth.contracts import Entity
    from mailauth.manifest import RunManifest
    from mailauth.p1_population.runner import _fill_from_co_jp_homepage

    e = Entity(
        entity_id="jp:2",
        run_id="2026-11",
        country="JP",
        population_ids=["jp-all-listed"],
        name="株式会社タクミナ",
        name_normalized="タクミナ",
        name_en="tacmina corporation",
    )
    manifest = RunManifest(run_id="2026-11", phase="p1_population", out_dir="/tmp/unused")
    _fill_from_co_jp_homepage([e], manifest, offline=True)
    assert e.official_domain is None
    assert "ENRICH_SKIPPED_CO_JP_HOMEPAGE" in {w.code for w in manifest.warnings}


def test_国内の母集団は最後にこの経路を使う():
    import yaml

    for name in ("jp-all-listed", "jp-prime", "jp-standard", "jp-growth", "jp-nikkei225"):
        enrich = yaml.safe_load(open(f"configs/populations/{name}.yaml", encoding="utf-8"))[
            "source"
        ]["enrich"]
        assert enrich[-1] == "co_jp_homepage", f"{name}: 最後に置いていない（{enrich}）"
