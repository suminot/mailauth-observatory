"""公式サイトのリンク先から同じ会社の別ドメインを拾う（BACKLOG 44）。

**リンクを張っているだけでは同じ会社ではない。** 検査の多くは
「取引先・SNS・取引所のドメインを付けない」ことを見ている。
テストはネットワークに出ない。
"""

from __future__ import annotations

from mailauth.site_links import SiteCompany, SiteLinkSource, is_private_hosting, link_domains


def _page(title: str, links: list[str] = ()) -> str:
    body = "".join(f'<a href="{u}">x</a>' for u in links)
    return f"<html><head><title>{title}</title></head><body>{body}</body></html>"


def test_リンク先の登録ドメインを取り出す_自分と共用ホスティングとメールは除く():
    page = (
        '<a href="https://www.brand.example.co.jp/item">a</a>'
        "<a href='https://shop.brand.example.co.jp/'>b</a>"
        '<a href="https://www.own.co.jp/ir/">c</a>'
        '<a href="https://renewal-dev.sakura.tv/">d</a>'
        '<a href="http://twitter.com/own">e</a>'
        '<a href="mailto:info@own.co.jp">f</a>'
        '<a href="/relative/path">g</a>'
    )
    assert link_domains(page, "own.co.jp") == ["example.co.jp", "twitter.com"]


def test_共用ホスティングの名前を見分ける():
    assert is_private_hosting("renewal-dev-fulltech.sakura.tv")
    assert is_private_hosting("x.github.io")
    assert not is_private_hosting("www.nifco-fastener.com")


class Web:
    def __init__(self, pages: dict[str, str], mx: set[str]) -> None:
        self.pages = pages
        self.mx = mx
        self.fetched: list[str] = []

    def fetch(self, url: str) -> str | None:
        self.fetched.append(url)
        return self.pages.get(url)

    def has_mx(self, domain: str) -> bool:
        return domain in self.mx


def _src(web: Web, **kw) -> SiteLinkSource:
    return SiteLinkSource(web.fetch, web.has_mx, concurrency=1, **kw)


def test_商号がタイトルにあり_MX_がある別ドメインだけを採る():
    web = Web(
        {
            "https://www.nifco.co.jp/": _page("株式会社ニフコ", [
                "https://www.nifco-fastener.com/",
                "https://www.partner.co.jp/",
                "https://www.nomx-brand.jp/",
            ]),
            "https://www.nifco-fastener.com/": _page("ファスナー | 株式会社ニフコ"),
            "https://www.partner.co.jp/": _page("株式会社パートナー"),
            "https://www.nomx-brand.jp/": _page("株式会社ニフコ ブランド"),
        },
        mx={"nifco-fastener.com", "partner.co.jp"},
    )
    src = _src(web)
    got = src.discover([SiteCompany("nifco.co.jp", "株式会社ニフコ", None, "JP")])
    assert got == {"nifco.co.jp": ["nifco-fastener.com"]}
    s = src.stats
    assert (s.found, s.no_mx, s.not_same_company) == (1, 1, 1)


def test_多くの会社が張っているドメインは第三者として捨てる():
    pages = {}
    companies = []
    for i, name in enumerate(["甲乙丙", "丁戊己", "庚辛壬"]):
        pages[f"https://www.c{i}.co.jp/"] = _page(f"株式会社{name}", ["https://www.jpx.co.jp/"])
        companies.append(SiteCompany(f"c{i}.co.jp", f"株式会社{name}", None, "JP"))
    # 取引所のページに偶然1社の商号があっても採らない（そもそも確かめに行かない）
    pages["https://www.jpx.co.jp/"] = _page("株式会社甲乙丙 上場のお知らせ")
    web = Web(pages, mx={"jpx.co.jp"})
    src = _src(web)
    assert src.discover(companies) == {}
    assert src.stats.shared_skipped == 3
    assert "https://www.jpx.co.jp/" not in web.fetched


def test_別の会社の起点にはリンクしても付けない():
    web = Web(
        {
            "https://www.parent.co.jp/": _page("株式会社親会社", ["https://www.child.co.jp/"]),
            "https://www.child.co.jp/": _page("株式会社親会社グループ 子会社"),
        },
        mx={"child.co.jp"},
    )
    src = _src(web)
    got = src.discover([
        SiteCompany("parent.co.jp", "株式会社親会社", None, "JP"),
        SiteCompany("child.co.jp", "株式会社子会社", None, "JP"),
    ])
    assert got == {}


def test_国内は商号の形でタイトルに無ければ採らない():
    """本文に社名が出るだけ（取引先の導入事例など）では同じ会社と言えない。"""
    web = Web(
        {
            "https://www.own.co.jp/": _page("株式会社甲乙丙", ["https://www.vendor.com/"]),
            "https://www.vendor.com/": (
                "<title>Vendor Inc.</title><body>導入事例：株式会社甲乙丙</body>"
            ),
        },
        mx={"vendor.com"},
    )
    src = _src(web)
    assert src.discover([SiteCompany("own.co.jp", "株式会社甲乙丙", None, "JP")]) == {}


def test_米国は英字社名がタイトルにあるものを採る():
    web = Web(
        {
            "https://www.acmeholdings.com/": _page("Acme Widgets Corporation", [
                "https://www.acmewidgets.co.uk/", "https://www.otherco.com/",
            ]),
            "https://www.acmewidgets.co.uk/": _page("Acme Widgets Corporation UK"),
            "https://www.otherco.com/": _page("Other Company Inc."),
        },
        mx={"acmewidgets.co.uk", "otherco.com"},
    )
    src = _src(web)
    got = src.discover([
        SiteCompany("acmeholdings.com", "Acme Widgets Corporation", None, "US"),
    ])
    assert got == {"acmeholdings.com": ["acmewidgets.co.uk"]}


def test_読めないサイトは数えて残す():
    src = _src(Web({}, mx=set()))
    assert src.discover([SiteCompany("down.co.jp", "株式会社甲乙丙", None, "JP")]) == {}
    assert (src.stats.sites, src.stats.unreadable) == (1, 1)


def test_1社あたり確かめに行く数に上限がある():
    links = [f"https://www.b{i}.jp/" for i in range(30)]
    web = Web({"https://www.own.co.jp/": _page("株式会社甲乙丙", links)}, mx=set())
    src = _src(web, max_candidates=5)
    src.discover([SiteCompany("own.co.jp", "株式会社甲乙丙", None, "JP")])
    assert src.stats.no_mx == 5


def test_全角の混じったリンクは半角にし_壊れた名前は捨てる():
    page = (
        '<a href="https://www.murata.coｍ/ja-jp">a</a>'
        '<a href="https://www.broken-/x">b</a>'
        '<a href="https://www.nakayama-">c</a>'
        '<a href="https://例え.テスト/">d</a>'
    )
    assert link_domains(page, "murata.co.jp") == ["murata.com"]


def test_読む途中の例外で止まらない():
    def fetch(url):
        raise ValueError("Invalid IDNA hostname")

    src = SiteLinkSource(fetch, lambda d: True, concurrency=1)
    assert src.discover([SiteCompany("own.co.jp", "株式会社甲乙丙", None, "JP")]) == {}
    assert src.stats.unreadable == 1


def test_持ち時間を超えたら残りは読まず数えて残す():
    t = [0.0]

    def clock():
        return t[0]

    def fetch(url):
        t[0] += 10  # 1本10秒かかる
        return _page("株式会社甲乙丙")

    companies = [SiteCompany(f"c{i}.co.jp", "株式会社甲乙丙", None, "JP") for i in range(5)]
    src = SiteLinkSource(fetch, lambda d: True, concurrency=1, budget_sec=25, clock=clock)
    src.discover(companies)
    assert src.stats.budget_skipped > 0
    assert src.stats.sites == 5


def test_同じ名前の別の_TLD_の候補():
    from mailauth.site_links import sibling_candidates

    assert sibling_candidates("global.canon", "JP") == ["canon.co.jp", "canon.com", "canon.jp"]
    assert sibling_candidates("www.zett.jp", "JP") == ["zett.co.jp", "zett.com"]
    assert sibling_candidates("acme.com", "US") == ["acme.net", "acme.co"]
    assert sibling_candidates("x.example", "DE") == []


def test_公式サイトに_MX_が無ければ同じ名前の別の_TLD_を商号で確かめて採る():
    web = Web(
        {
            "https://www.global.canon/": _page("キヤノン株式会社"),
            "https://www.canon.co.jp/": _page("キヤノン株式会社 | Canon"),
            # 同じ名前の別の会社。タイトルに商号が無いので採らない
            "https://www.canon.com/": _page("Canon Outdoor Supplies LLC"),
            "https://www.hasmx.co.jp/": _page("株式会社甲乙丙"),
        },
        mx={"canon.co.jp", "canon.com", "hasmx.co.jp", "hasmx.com"},
    )
    src = _src(web)
    got = src.discover([
        SiteCompany("global.canon", "キヤノン株式会社", None, "JP"),
        # 公式サイトに MX があれば別の TLD は見に行かない
        SiteCompany("hasmx.co.jp", "株式会社甲乙丙", None, "JP"),
    ])
    assert got == {}
    assert src.siblings == {"global.canon": ["canon.co.jp"]}
    assert (src.stats.sibling_checked, src.stats.sibling_found) == (1, 1)
    assert "https://www.hasmx.com/" not in web.fetched


def test_同じ名前の別の_TLD_はリンク先より先に確かめる():
    """持ち時間が少なくても、メールのドメインが見つかっていない会社を先にする。"""
    t = [0.0]
    links = [f"https://www.link{i}.co.jp/" for i in range(10)]
    pages = {"https://www.global.canon/": _page("キヤノン株式会社", links),
             "https://www.canon.co.jp/": _page("キヤノン株式会社")}

    def fetch(url):
        t[0] += 1
        return pages.get(url) or _page("株式会社別会社")

    mx = {"canon.co.jp"} | {f"link{i}.co.jp" for i in range(10)}
    src = SiteLinkSource(fetch, lambda d: d in mx, concurrency=1, budget_sec=4,
                         clock=lambda: t[0])
    src.discover([SiteCompany("global.canon", "キヤノン株式会社", None, "JP")])
    assert src.siblings == {"global.canon": ["canon.co.jp"]}
    assert src.stats.budget_skipped > 0


def test_co_jp_は本文に社名があれば認め_それ以外はタイトルの商号の形を求める():
    from mailauth.site_links import names_company

    company = SiteCompany("global.example", "株式会社キヤノン電子技研", None, "JP")
    page = "<title>Welcome</title><body>キヤノン電子技研のサイトです</body>"
    assert names_company(page, company, "example.co.jp")
    assert not names_company(page, company, "example.com")
    assert not names_company(page, company, "example.jp")


def test_持株会社の接尾辞を外した名前も候補にする():
    from mailauth.site_links import sibling_candidates

    assert sibling_candidates("nitorihd.co.jp", "JP") == [
        "nitorihd.com", "nitorihd.jp", "nitori.co.jp", "nitori.com", "nitori.jp",
    ]
    assert "daiwa.co.jp" in sibling_candidates("daiwa-grp.jp", "JP")
    assert "smc.jp" in sibling_candidates("smcworld.com", "JP")
    assert "epson.co.jp" in sibling_candidates("corporate.epson", "JP")
    # 短すぎる名前にはしない
    assert "ab.co.jp" not in sibling_candidates("abhd.co.jp", "JP")


def test_同じ名前の別の_TLD_は公式サイトを指していれば採り_似ただけの別会社は採らない():
    web = Web(
        {
            "https://www.corporate.epson/": _page("セイコーエプソン株式会社"),
            # 商号は無いが、公式サイトへリンクしている
            "https://www.epson.jp/": _page("エプソン ホームページ", ["https://corporate.epson/ja/"]),
            # 名前が似ただけの別会社。公式サイトを指さない
            "https://www.epson.com/": _page("Epson Outdoor LLC", ["https://www.other.example/"]),
        },
        mx={"epson.jp", "epson.com"},
    )
    src = _src(web)
    src.discover([SiteCompany("corporate.epson", "セイコーエプソン株式会社", None, "JP")])
    assert src.siblings == {"corporate.epson": ["epson.jp"]}
