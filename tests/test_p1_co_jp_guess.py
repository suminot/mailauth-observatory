"""英字社名から .co.jp を推測し、そのサイトに社名が載っているかで確かめる（BACKLOG 15）。

例はすべて 2026-10-31 の試験（国内の起点なし30社）で実際に出たもの。
テストは一切ネットワークに出ない。DNS とページは差し替える。
"""

from __future__ import annotations

from mailauth.p1_population.co_jp_guess import (
    CoJpGuesser,
    decode_page,
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
    assert guesses("Yokogawa Bridge Holdings Corp.")[:2] == [
        "yokogawabridge.co.jp",
        "yokogawa-bridge.co.jp",
    ]
    assert guesses("Yokogawa Bridge Holdings Corp.")[-1] == "yokogawa.co.jp"
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


def test_短い社名は_ページのどこかに載っているだけでは採らない():
    """**短い語は他社のページにも載る。** 2026-11 の国内計測で埋めた 177 社の
    誤りは、すべて社名の芯が4文字以下の会社だった。例はそのときのタイトル。
    """
    # 本文に「ムサシ」が出てくるが、タイトルは別の会社
    assert not page_names_company(
        "<title>武蔵精密工業株式会社</title><p>ムサシの技術</p>", "ムサシ"
    )
    # 芯がタイトルに入っていても、商号の形でなければ採らない
    assert not page_names_company("<title>ホーム | JRC 日本無線株式会社</title>", "JRC")
    assert not page_names_company(
        "<title>まつ毛美容液 | まつ毛と目元の専門ブランド eyez アイズ</title>", "アイズ"
    )
    # 商号の形なら採る。英字は大文字小文字を問わない
    assert page_names_company("<title>株式会社タクミナ</title>", "タクミナ")
    assert page_names_company("<title>Ｓｐｋ株式会社｜自動車部品</title>", "SPK")
    assert page_names_company(
        '<meta property="og:site_name" content="ニッコンホールディングス">', "ニッコン"
    )


def test_長い社名は本文に載っていれば採る():
    core = name_core("株式会社テクノ菱和")
    assert core == "テクノ菱和"
    assert page_names_company("<title>トップ</title><footer>© 株式会社テクノ菱和</footer>", core)


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
    web = _Web(
        {"muraki.co.jp"},
        {"https://www.muraki.co.jp/": "<title>ムラキ株式会社 | 会社案内</title><h1>ムラキ</h1>"},
    )
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
    # www が読めなければ裸のドメイン、https で読めなければ http も試している
    assert web.fetched == [
        "https://www.shinden.co.jp/",
        "https://shinden.co.jp/",
        "http://www.shinden.co.jp/",
        "http://shinden.co.jp/",
    ]


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
            "https://www.muraki.co.jp/": "<title>ムラキ株式会社</title>",
            "https://www.tacmina.co.jp/": "<title>株式会社タクミナ</title>",
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


def test_持株会社とフィナンシャルグループは決まった形も試す():
    """`HOLDINGS` / `GROUP` は語として落とすので、つなげた形だけでは
    tokyu-fudosan-hd.co.jp や mebuki-fg.co.jp に届かない。"""
    got = guesses("Tokyu Fudosan Holdings Corporation")
    assert got[:2] == ["tokyufudosan.co.jp", "tokyu-fudosan.co.jp"]
    for form in (
        "tokyu-fudosan-hd.co.jp",
        "tokyufudosanhd.co.jp",
        "tokyu-fudosan-holdings.co.jp",
        "tokyufudosanholdings.co.jp",
    ):
        assert form in got
    # 先頭の語だけの形（他社のことが多い）は、決まった形より後に試す
    assert got.index("tokyu.co.jp") > got.index("tokyu-fudosan-hd.co.jp")

    fg = guesses("Mebuki Financial Group, Inc.")
    assert "mebuki-fg.co.jp" in fg and "mebukifg.co.jp" in fg

    # 持株会社でなければ今までどおり
    assert guesses("MURAKI CORPORATION") == ["muraki.co.jp"]


def test_決まった形でも確かめはページで行う():
    """推測が DNS に実在しても、日本語の商号が載っていなければ採らない。"""
    pages = {
        "https://www.tokyu-fudosan-hd.co.jp/": "<title>東急不動産ホールディングス株式会社</title>",
    }
    g = CoJpGuesser(
        exists=lambda d: d in {"tokyu-fudosan-hd.co.jp", "tokyu.co.jp"},
        fetch=lambda url: pages.get(url, "<title>別の会社</title>"),
    )
    assert g.domain_for("東急不動産ホールディングス株式会社", "Tokyu Fudosan Holdings") == (
        "tokyu-fudosan-hd.co.jp"
    )


def test_https_で読めなければ_http_で確かめる():
    """中間証明書を送っていないサイトや http だけのサイトが「読めない」に
    入っていた（2026-11 の国内で 606 社中 97 社）。"""
    web = _Web(
        {"shinden.co.jp"},
        {"http://www.shinden.co.jp/": "<title>シンデン・ハイテックス株式会社</title>"},
    )
    g = CoJpGuesser(web.exists, web.fetch)
    assert g.domain_for("シンデン・ハイテックス株式会社", "Shinden Hightex") == "shinden.co.jp"
    # https で読めた入口があれば http は見ない
    web2 = _Web(
        {"shinden.co.jp"},
        {"https://www.shinden.co.jp/": "<title>別の会社</title>"},
    )
    g2 = CoJpGuesser(web2.exists, web2.fetch)
    assert g2.domain_for("シンデン・ハイテックス株式会社", "Shinden Hightex") is None
    assert web2.fetched == ["https://www.shinden.co.jp/"]
    assert g2.stats.name_not_on_page == 1


def test_charset_をヘッダに書かないサイトは_meta_で読む():
    """ヘッダに charset が無いと HTTP ライブラリは UTF-8 で読み、Shift_JIS の
    ページの商号が化けて「社名が載っていない」に数えていた。"""
    page = '<html><head><meta charset="Shift_JIS"><title>株式会社ムラキ</title>'
    body = page.encode("cp932")
    text = decode_page(body, None)
    assert "株式会社ムラキ" in text
    assert page_names_company(text, "ムラキ")
    # http-equiv の形
    body2 = (
        '<meta http-equiv="Content-Type" content="text/html; charset=EUC-JP">日本無線株式会社'
    ).encode("euc_jp")
    assert "日本無線株式会社" in decode_page(body2, None)
    # 宣言が無くても、UTF-8 で読めなければ cp932 を試す
    assert "株式会社ムラキ" in decode_page("株式会社ムラキ".encode("cp932"), None)
    # ヘッダの宣言が先
    assert decode_page("ムラキ".encode(), "utf-8") == "ムラキ"
    # 知らない名前は無視して先へ進む
    assert decode_page("ムラキ".encode(), "x-unknown") == "ムラキ"


def test_並行に確かめても数え上げがずれない():
    import threading

    from mailauth.ordered import in_input_order

    names = [(f"株式会社テスト{i:03d}", f"Test{i:03d} Corporation") for i in range(60)]
    pages = {f"https://www.test{i:03d}.co.jp/": f"株式会社テスト{i:03d}" for i in range(0, 60, 2)}
    lock = threading.Lock()
    fetched: list[str] = []

    def fetch(url):
        with lock:
            fetched.append(url)
        return pages.get(url)

    g = CoJpGuesser(lambda d: True, fetch)
    got = list(in_input_order(lambda n: g.domain_for(*n), names, concurrency=8, window=16))
    assert got == [f"test{i:03d}.co.jp" if i % 2 == 0 else None for i in range(60)]
    assert g.stats.attempted == 60
    assert g.stats.found == 30
    assert g.stats.pages_fetched == len(fetched)


# ---------------------------------------------------------------------------
# 英字社名が無い会社は、EDINET の読み（ヨミ）から推測する
# ---------------------------------------------------------------------------


def test_読みをヘボン式でつなげる():
    from mailauth.p1_population.co_jp_guess import kana_guesses, kana_to_romaji

    assert kana_to_romaji("タイヘイデンギョウ") == "taiheidengyou"
    assert kana_to_romaji("ムラキ") == "muraki"
    assert kana_to_romaji("ニッケンコウガク") == "nikkenkougaku"
    # 促音の後の ch は tch（ヘボン式）
    assert kana_to_romaji("マッチ") == "matchi"
    # ひらがな・半角カナ・長音・中黒
    assert kana_to_romaji("たいへい") == "taihei"
    assert kana_to_romaji("ﾑﾗｷ") == "muraki"
    assert kana_to_romaji("ホールディングス") == ""
    # 法人格は落とす
    assert kana_to_romaji("カブシキガイシャムラキ") == "muraki"
    # 長音を詰めた形を先に試す
    assert kana_guesses("タイヘイデンギョウ") == [
        "taiheidengyo.co.jp",
        "taiheidengyou.co.jp",
    ]
    assert kana_guesses("ムラキ") == ["muraki.co.jp"]
    assert kana_guesses("") == []
    assert kana_guesses(None) == []


def test_英字社名が無ければ読みから推測し_ページで確かめる():
    web = _Web(
        {"taiheidengyo.co.jp"},
        {"https://www.taiheidengyo.co.jp/": "<title>太平電業株式会社</title>"},
    )
    g = CoJpGuesser(web.exists, web.fetch)
    assert g.domain_for("太平電業株式会社", None, "タイヘイデンギョウ") == "taiheidengyo.co.jp"
    assert (g.stats.from_kana, g.stats.found_from_kana, g.stats.no_english_name) == (1, 1, 0)
    # 読みからの推測でも、商号が載っていなければ採らない
    web2 = _Web({"taiheidengyo.co.jp"}, {"https://www.taiheidengyo.co.jp/": "<title>別</title>"})
    g2 = CoJpGuesser(web2.exists, web2.fetch)
    assert g2.domain_for("太平電業株式会社", None, "タイヘイデンギョウ") is None
    assert g2.stats.name_not_on_page == 1
    # 英字社名があれば読みは使わない
    web3 = _Web(set(), {})
    g3 = CoJpGuesser(web3.exists, web3.fetch)
    g3.domain_for("ムラキ株式会社", "MURAKI CORPORATION", "ムラキ")
    assert g3.stats.from_kana == 0


def test_コードリストの読みを_P1_の推測に渡す(tmp_path):
    """EDINET コードリストの「提出者名（ヨミ）」列を読み、edinet_code で引く。"""
    from mailauth.contracts import Entity
    from mailauth.manifest import RunManifest
    from mailauth.p1_population.runner import _fill_from_co_jp_homepage

    e = Entity(
        entity_id="jp:3",
        run_id="2026-11",
        country="JP",
        population_ids=["jp-all-listed"],
        name="太平電業株式会社",
        name_normalized="太平電業",
        edinet_code="E00999",
    )
    web = _Web(
        {"taiheidengyo.co.jp"},
        {"https://www.taiheidengyo.co.jp/": "<title>太平電業株式会社</title>"},
    )
    manifest = RunManifest(run_id="2026-11", phase="p1_population", out_dir=str(tmp_path))
    _fill_from_co_jp_homepage(
        [e],
        manifest,
        guesser=CoJpGuesser(web.exists, web.fetch),
        kana={"E00999": "タイヘイデンギョウ"},
    )
    assert e.official_domain == "taiheidengyo.co.jp"
    assert manifest.breakdown["co_jp_homepage"]["found_from_kana"] == 1


def test_コードリストのヨミ列を読む(edinet_sample):
    from mailauth.config import load_population
    from mailauth.p1_population.edinet import parse_code_list

    cfg = load_population("configs/populations/jp-all-listed.yaml")
    rows, _ = parse_code_list(edinet_sample, cfg.source.edinet_code_list)
    assert rows[0].name_kana == "サンプルジョウホウシステム"


def test_外れた理由を見本として残す():
    """「社名が載っていない」「読めない」の中身を次の判断に使う。"""
    web = _Web(
        {"shinden.co.jp", "muraki.co.jp"},
        {"https://www.shinden.co.jp/": "<title>別の会社</title>"},
    )
    g = CoJpGuesser(web.exists, web.fetch)
    g.domain_for("シンデン・ハイテックス株式会社", "Shinden Hightex")
    g.domain_for("ムラキ株式会社", "MURAKI")
    d = g.stats.to_dict()
    assert d["sample_not_on_page"] == ["shinden.co.jp 「別の会社」 ← シンデンハイテックス"]
    assert d["sample_unreachable"] == ["muraki.co.jp"]


# ---------------------------------------------------------------------------
# ページが読めなければ、証明書の組織名（O）で確かめる
# ---------------------------------------------------------------------------


def test_証明書の組織名は法人格を除いて完全に一致したときだけ採る():
    from mailauth.p1_population.co_jp_guess import org_matches

    assert org_matches("SecuAvail Inc.", "SecuAvail Inc.")
    assert org_matches("NIRECO CORPORATION", "Nireco Corporation")
    # 部分一致は採らない
    assert not org_matches("Yamato Transport Co., Ltd.", "YAMATO CORPORATION")
    assert not org_matches("Yamato Holdings Co., Ltd.", "Yamato Transport Co., Ltd.")
    # 短い社名は偶然に当たる
    assert not org_matches("NAC Co., Ltd.", "NAC CO., LTD.")
    assert not org_matches(None, "SecuAvail Inc.")
    assert not org_matches("SecuAvail Inc.", None)


def test_ページが読めないときは証明書の組織名で確かめる():
    """WAF が見慣れない User-Agent を 403 で断っても、TLS の握手は終わっている。"""
    web = _Web({"secuavail.co.jp"}, {})  # どの入口も読めない
    orgs = {"secuavail.co.jp": "SecuAvail Inc."}
    g = CoJpGuesser(web.exists, web.fetch, cert_org=orgs.get)
    assert g.domain_for("株式会社セキュアヴェイル", "SecuAvail Inc.") == "secuavail.co.jp"
    assert (g.stats.found, g.stats.found_by_cert_org, g.stats.page_unreachable) == (1, 1, 0)


def test_証明書が別の会社なら採らず_読めないに数える():
    web = _Web({"sansei.co.jp"}, {})
    g = CoJpGuesser(web.exists, web.fetch, cert_org={"sansei.co.jp": "SANSEI LANDIC"}.get)
    assert g.domain_for("株式会社三精テクノロジーズ", "SANSEI TECHNOLOGIES INC.") is None
    assert g.stats.page_unreachable == 1
    # DV 証明書（組織名が無い）でも同じ
    g2 = CoJpGuesser(web.exists, web.fetch, cert_org=lambda d: None)
    assert g2.domain_for("株式会社三精テクノロジーズ", "SANSEI TECHNOLOGIES INC.") is None


def test_ページが読めたときは証明書を見ない():
    """**ページに社名が無いのは「違った」である。** 証明書で上書きしない。"""
    asked: list[str] = []

    def cert_org(domain):
        asked.append(domain)
        return "Kanda Tsushinki Co., Ltd."

    web = _Web({"kanda.co.jp"}, {"https://www.kanda.co.jp/": "<title>神田通信工業株式会社</title>"})
    g = CoJpGuesser(web.exists, web.fetch, cert_org=cert_org)
    assert g.domain_for("神田通信機株式会社", "Kanda Tsushinki Co., Ltd.") is None
    assert asked == []


# -- .co.jp に無ければ .jp・.com ------------------------------------------------
#
# 2026-11 の国内で起点が取れなかった会社には、ricksoft.jp・modalis.com の
# ように .co.jp を持たない会社が並んでいた。.jp・.com は1組織1つの決まりが
# 無いので、**社名の長さに関わらずタイトルに商号の形を求める。**


def test_co_jpに無ければcomを見る():
    from mailauth.p1_population.co_jp_guess import CoJpGuesser

    web = _Web(
        {"modalis.com"},
        {"https://www.modalis.com/": "<title>株式会社モダリス | Modalis</title>"},
    )
    g = CoJpGuesser(web.exists, web.fetch)
    assert g.domain_for("株式会社モダリス", "Modalis Therapeutics Corporation") == "modalis.com"
    assert g.stats.found == 1
    assert g.stats.found_other_tld == 1
    assert g.stats.sample_found_other_tld == ["modalis.com ← モダリス"]


def test_co_jpが当たればjpもcomも見ない():
    """**.co.jp が先。** 1組織1つの決まりがある方で確かめられれば、それで足りる。"""
    from mailauth.p1_population.co_jp_guess import CoJpGuesser

    web = _Web(
        {"muraki.co.jp", "muraki.jp", "muraki.com"},
        {
            "https://www.muraki.co.jp/": "<title>ムラキ株式会社</title>",
            "https://www.muraki.com/": "<title>ムラキ株式会社</title>",
        },
    )
    g = CoJpGuesser(web.exists, web.fetch)
    assert g.domain_for("ムラキ株式会社", "MURAKI CORPORATION") == "muraki.co.jp"
    assert not any("muraki.com" in u or "muraki.jp" in u for u in web.fetched)
    assert g.stats.found_other_tld == 0


def test_jpとcomは長い社名でも本文だけでは採らない():
    """他社の「導入事例」や取引先一覧にも、長い社名は載る。

    .co.jp なら持ち主は1組織に限られるが、.jp・.com はそうではない。
    """
    from mailauth.p1_population.co_jp_guess import CoJpGuesser

    page = "<title>導入事例</title><li>株式会社ネットプロテクションズ 様</li>"
    web = _Web({"netprotections.com"}, {"https://www.netprotections.com/": page})
    g = CoJpGuesser(web.exists, web.fetch)
    got = g.domain_for("株式会社ネットプロテクションズホールディングス", "Net Protections Inc.")
    assert got is None
    assert g.stats.name_not_on_page == 1
    assert g.stats.found_other_tld == 0


def test_jpはcomより先に見る():
    from mailauth.p1_population.co_jp_guess import other_tld_guesses

    assert other_tld_guesses(["ricksoft.co.jp", "rick-soft.co.jp"]) == [
        "ricksoft.jp",
        "rick-soft.jp",
        "ricksoft.com",
        "rick-soft.com",
    ]


def test_確かめられなかった理由は分かったことの多い方を1つ数える():
    """.co.jp は DNS に無く、.com は読めて違った ── **「違った」に1つだけ数える。**

    両方を数えると、内訳の合計が試した社数を超えて読めなくなる。
    """
    from mailauth.p1_population.co_jp_guess import CoJpGuesser

    web = _Web(
        {"porters.com"},
        {"https://www.porters.com/": "<title>Porters Pizza</title>"},
    )
    g = CoJpGuesser(web.exists, web.fetch)
    assert g.domain_for("ポーターズ株式会社", "PORTERS CORPORATION") is None
    s = g.stats
    assert (s.no_guess_exists, s.name_not_on_page, s.page_unreachable) == (0, 1, 0)
    assert s.sample_not_on_page == ["porters.com 「PortersPizza」 ← ポーターズ"]


def test_どこにも無ければDNSに無かったと数える():
    from mailauth.p1_population.co_jp_guess import CoJpGuesser

    g = CoJpGuesser(lambda d: False, lambda u: None)
    assert g.domain_for("ポーターズ株式会社", "PORTERS CORPORATION") is None
    assert g.stats.no_guess_exists == 1
