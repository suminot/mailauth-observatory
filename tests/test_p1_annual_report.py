"""年次報告書の本文から公式サイトを取る（BACKLOG 15）。

**SEC の submissions は website 欄を持たない**（6,068社全件で0件）。
10-K の「Available Information」で会社が自分で書いたドメインを読む。

**取り逃がしより、別の会社のドメインを付けるほうが悪い。** 検査の大半は
「拾ってはいけないものを拾わない」ことを見ている。

テストは一切ネットワークに出ない。取得層は差し替える。
"""

from __future__ import annotations

import json

import pytest

from mailauth.p1_population import run as run_p1
from mailauth.p1_population.annual_report import (
    AnnualReport,
    AnnualReportWebsites,
    latest_annual_report,
    pick_domain,
    stated_domains,
)
from mailauth.paths import phase_output

RUN = "2026-08"
CONFIG = "configs/populations/us-all-listed.yaml"


def _domains(text: str) -> dict[str, int]:
    found, _, _ = stated_domains(text)
    return dict(found)


# ===========================================================================
# 本文から読む
# ===========================================================================


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 実際の 10-K の書き方（2026-10-18 の標本から）
        ("Our website address is www.zillowgroup.com.", "zillowgroup.com"),
        ("Our internet address is www.ghco.com. We make available", "ghco.com"),
        (
            "on our website at https://investor.example.com/sec-filings free of charge",
            "example.com",
        ),
        ("The Company's website is www.tenn.gr and", "tenn.gr"),
        ("Our corporate website is located at sweetgreen.com.", "sweetgreen.com"),
        # 日本の子会社などで2段の公開接尾辞を持つもの
        ("Our website is www.example.co.jp.", "example.co.jp"),
        # **タグを消すと語がくっつく。** 空白に置き換えていれば読める
        ("<p>Our website:</p><td>www.acme-corp.com</td>", "acme-corp.com"),
        # 実体参照
        ("Our&#160;website&nbsp;is&#160;www.nbsp-test.com.", "nbsp-test.com"),
    ],
)
def test_自社のサイトとして書かれたドメインを読む(text, expected):
    assert pick_domain(stated_domains(text)[0]) == expected


def test_自社のと書いていない_URL_は拾わない():
    """**所有の根拠は「our」である。** 文書中の任意の URL は他社のものが混ざる。"""
    text = (
        "Reports are available at www.sec.gov. Our auditor, Deloitte "
        "(www.deloitte.com), audited the statements. Visit www.partner.com."
    )
    assert _domains(text) == {}


def test_自社のサイトの句から離れた_URL_は拾わない():
    """**句の直後だけを見る。** 同じ文書の離れた箇所にある他社の URL を拾わない。

    上の検査は「our website」が1度も出てこない文なので、句の直後に
    限っているかどうかを見ていなかった（壊して確かめたら素通りした）。
    """
    filler = "We describe our business in this section. " * 10
    text = f"Visit our partner at www.partner.com. {filler} Our website is www.acme.com."
    assert _domains(text) == {"acme.com": 1}


def test_タグの切れ目で語がくっつかない():
    """**タグは空白に置き換える。** 消すと次の語とくっつき、TLD が壊れる。

    `www.acme.com</p><p>Information` を消すと `www.acme.comInformation` になる。
    """
    text = "<p>Our website is www.acme.com</p><p>Information on it is not part of this report"
    assert _domains(text) == {"acme.com": 1}


def test_SEC_のサイトを自社のものにしない():
    """「our website or the SEC's website at www.sec.gov」の形が多い。"""
    text = "available on our website or through the SEC's website at www.sec.gov."
    found, _, dropped = stated_domains(text)
    assert dict(found) == {}
    # 「our website」の直後の窓に sec.gov が入るが、第三者として捨てる
    assert dropped == 1


def test_句ごとに最初の1つだけ数える():
    """後ろに続く第三者のドメインを、自社のものとして数えない。"""
    text = "Our website is www.acme.com and our filings are also at www.othersite.com"
    # 2つ目の「our」は「our filings」で、サイトの句ではない
    assert _domains(text) == {"acme.com": 1}


@pytest.mark.parametrize(
    "text",
    [
        # 文の切れ目が詰まったもの。2文字の TLD に見える
        "our website. See Acme Corp.In addition, we",
        # ファイル名
        "our website at Form10-K.htm",
        # 知らない TLD（3文字以上で一覧に無い）
        "our website, see Item 10-K.The following",
        # メールアドレスのドメイン部
        "our website or email ir@acme.com",
    ],
)
def test_ドメインに見えるだけのものは拾わない(text):
    assert _domains(text) == {}


def test_2文字の_TLD_も_www_付きなら読む():
    assert _domains("Our website is www.example.de.") == {"example.de": 1}


def test_一番多く書かれたものを採る():
    """IR 専用ドメインと本体が並ぶことがある（2026-10-18 の BBB Foods）。"""
    text = (
        "Our website is www.tiendas3b.com. Our investor relations website is "
        "www.investorstiendas3b.com. Information on our website www.tiendas3b.com"
    )
    found, hosts, _ = stated_domains(text)
    assert pick_domain(found) == "tiendas3b.com"
    assert hosts["tiendas3b.com"] == "www.tiendas3b.com"


def test_書かれていなければ何も返さない():
    assert pick_domain(stated_domains("No website is mentioned here.")[0]) is None


# ===========================================================================
# 最新の年次報告書を探す
# ===========================================================================


def _submissions(*filings: tuple[str, str, str]) -> dict:
    return {
        "filings": {
            "recent": {
                "form": [f for f, _, _ in filings],
                "accessionNumber": [a for _, a, _ in filings],
                "primaryDocument": [d for _, _, d in filings],
                "filingDate": ["2026-02-01"] * len(filings),
            }
        }
    }


def test_最新の年次報告書を選ぶ():
    subs = _submissions(
        ("8-K", "0001-26-000003", "a.htm"),
        ("10-K/A", "0001-26-000002", "b.htm"),  # 訂正は本文が一部だけ
        ("10-K", "0001-26-000001", "c.htm"),
        ("10-K", "0001-25-000001", "d.htm"),
    )
    report = latest_annual_report("0000000001", subs)
    assert report is not None
    assert (report.form, report.document) == ("10-K", "c.htm")
    assert report.url == (
        "https://www.sec.gov/Archives/edgar/data/1/000126000001/c.htm"
    )


def test_外国企業は_20F_を読む():
    report = latest_annual_report("0000000002", _submissions(("20-F", "0002-26-1", "x.htm")))
    assert report is not None and report.form == "20-F"


def test_年次報告書が無ければ_None():
    """SPAC や新規上場は年次報告書をまだ出していない。"""
    assert latest_annual_report("0000000003", _submissions(("S-1", "0003-26-1", "s.htm"))) is None
    assert latest_annual_report("0000000003", None) is None


# ===========================================================================
# 取得とキャッシュ
# ===========================================================================


class _FakeClient:
    def __init__(self, tmp_path, pages: dict[str, str | Exception | None]):
        self.cache_dir = tmp_path / "sec"
        self.pages = pages
        self.calls: list[str] = []

    def get_text(self, url, *, max_bytes):
        self.calls.append(url)
        page = self.pages.get(url)
        if isinstance(page, Exception):
            raise page
        return page


def _report(n: int = 1) -> AnnualReport:
    return AnnualReport(
        cik=f"{n:010d}", form="10-K", accession=f"0000-26-{n:06d}", document="k.htm"
    )


def test_取れたら書かれたホストのまま返す(tmp_path):
    """`www.` をこちらで足さない。存在しない名前を作ることがある。"""
    report = _report()
    client = _FakeClient(tmp_path, {report.url: "Our website is investor.acme.com."})
    reader = AnnualReportWebsites(client)
    assert reader.website_for(report) == "https://investor.acme.com"
    assert reader.stats.found == 1


def test_提出済みの書類は二度落とさない(tmp_path):
    """受付番号で結果を持ち越す。**取れなかった結果も持ち越す。**"""
    found, missing = _report(1), _report(2)
    client = _FakeClient(
        tmp_path,
        {found.url: "Our website is www.acme.com.", missing.url: "nothing here"},
    )
    AnnualReportWebsites(client).website_for(found)
    AnnualReportWebsites(client).website_for(missing)

    again = AnnualReportWebsites(client)
    assert again.website_for(found) == "https://www.acme.com"
    assert again.website_for(missing) is None
    assert len(client.calls) == 2
    assert again.stats.cache_hit == 2
    assert again.stats.not_stated == 1

    path = client.cache_dir / "annual_website" / f"{found.accession}.json"
    cached = json.loads(path.read_text())
    assert cached["domain"] == "acme.com"


def test_読めなかったのと書いていなかったのを分ける(tmp_path):
    """原則5。失敗は持ち越さない ── 次の実行で読み直す。"""
    failed, silent = _report(1), _report(2)
    client = _FakeClient(
        tmp_path, {failed.url: RuntimeError("503"), silent.url: "no site"}
    )
    reader = AnnualReportWebsites(client)
    assert reader.website_for(failed) is None
    assert reader.website_for(silent) is None
    assert reader.website_for(None) is None
    assert (reader.stats.fetch_failed, reader.stats.not_stated, reader.stats.no_annual_report) == (
        1,
        1,
        1,
    )
    assert not (client.cache_dir / "annual_website" / f"{failed.accession}.json").exists()


# ===========================================================================
# P1 の米国母集団に入っていること
# ===========================================================================


def test_米国母集団が年次報告書を読む設定になっている():
    import yaml

    data = yaml.safe_load(open(CONFIG, encoding="utf-8"))
    enrich = data["source"]["enrich"]
    assert "sec_annual_report" in enrich
    # **Wikidata の後。** 1社1文書を落とす重い経路なので、軽い経路で
    # 埋まらなかった分だけを読む
    assert enrich.index("sec_annual_report") > enrich.index("wikidata_identity")


def test_引いていないときはそう書く():
    """offline の実行で「サイトの記述が無い」と読ませない（原則5）。"""
    from mailauth.p1_population.sec_edgar import ListedRow

    rows = [ListedRow(cik="0000320193", name="Apple Inc.", ticker="AAPL", exchange="Nasdaq")]
    result = run_p1(config=CONFIG, run_id=RUN, offline=True, rows=rows)
    codes = {w["code"] for w in result["warnings"]}
    assert "ENRICH_SKIPPED_SEC_ANNUAL_REPORT" in codes


def test_起点の無い企業だけを年次報告書で埋める(monkeypatch, tmp_path):
    """**Wikidata で埋まった企業の書類は読まない。上書きもしない。**"""
    from mailauth.p1_population import sec_edgar, wikidata
    from mailauth.p1_population.sec_edgar import ListedRow, SecFacts

    rows = [
        ListedRow(cik="0000000001", name="Wiki Co", ticker="WIKI", exchange="NYSE"),
        ListedRow(cik="0000000002", name="Report Co", ticker="RPT", exchange="NYSE"),
        ListedRow(cik="0000000003", name="Silent Co", ticker="SLNT", exchange="NYSE"),
    ]
    pages = {
        _report(1).url: "Our website is www.wrong-for-wiki.com.",
        _report(2).url: "Our website address is www.reportco.com.",
        _report(3).url: "We have no site.",
    }

    class _Client(_FakeClient):
        def __init__(self, *a, **k):
            super().__init__(tmp_path, pages)
            self.stats = sec_edgar.SecStats()

        def facts_for_cik(self, cik):
            return SecFacts(cik=cik, annual_report=_report(int(cik)))

        def close(self):
            pass

    client_holder: dict = {}

    def _make(*a, **k):
        client_holder["c"] = _Client()
        return client_holder["c"]

    monkeypatch.setattr(sec_edgar, "SecEdgarClient", _make)
    monkeypatch.setattr(sec_edgar, "load_exchange_listing", lambda c, exchanges: (rows, {}))
    monkeypatch.setattr(
        wikidata,
        "fetch_cik_identity",
        lambda q: ({"0000000001": {"website": "https://www.wikico.com"}}, {}),
    )
    monkeypatch.setattr(
        wikidata, "fetch_identity", lambda *a, **k: ({}, {"key_count": 0, "unusable_keys": 0})
    )

    result = run_p1(config=CONFIG, run_id=RUN, offline=False)

    from mailauth.io import read_parquet

    df = read_parquet(phase_output(RUN, "p1_population", "entities.parquet")).set_index("cik")
    assert df.loc["0000000001", "official_domain"] == "wikico.com"
    assert df.loc["0000000002", "official_domain"] == "reportco.com"
    assert df.loc["0000000002", "official_url"] == "https://www.reportco.com"
    assert df["official_domain"].isna().sum() == 1

    # Wikidata で埋まった会社の書類は落としていない
    assert _report(1).url not in client_holder["c"].calls
    stats = result["breakdown"]["sec_annual_report"]
    assert (stats["missing_before"], stats["found"], stats["not_stated"]) == (2, 1, 1)
    assert stats["still_missing"] == 1
