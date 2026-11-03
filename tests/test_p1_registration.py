"""年次報告書がまだ無い会社は、目論見書・登録届出から公式サイトを読む（BACKLOG 28）。

読み方は年次報告書と同じ（`stated_domains`）。ここでは**どの届出を選ぶか**と、
**他社に付いているドメインを黙って採らない**ことを見る。

テストは一切ネットワークに出ない。取得層は差し替える。
"""

from __future__ import annotations

import yaml

from mailauth.p1_population import run as run_p1
from mailauth.p1_population.annual_report import (
    THIRD_PARTY,
    AnnualReport,
    AnnualReportWebsites,
    latest_proxy,
    latest_registration,
    renamed_at,
)
from mailauth.paths import phase_output

RUN = "2026-08"
CONFIG = "configs/populations/us-all-listed.yaml"


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


def test_最新の目論見書を選ぶ():
    subs = _submissions(
        ("8-K", "0001-26-000004", "a.htm"),
        ("424B4", "0001-26-000003", "p.htm"),
        ("S-1/A", "0001-26-000002", "s2.htm"),
        ("S-1", "0001-26-000001", "s1.htm"),
    )
    reg = latest_registration("0000000001", subs)
    assert reg is not None and (reg.form, reg.document) == ("424B4", "p.htm")


def test_年次報告書や_8K_は目論見書として読まない():
    subs = _submissions(("10-K", "0001-26-1", "k.htm"), ("8-K", "0001-26-2", "e.htm"))
    assert latest_registration("0000000001", subs) is None
    assert latest_registration("0000000001", None) is None


def test_米国母集団は年次報告書のあと_プレスリリースの前に目論見書と委任状説明書を読む():
    enrich = yaml.safe_load(open(CONFIG, encoding="utf-8"))["source"]["enrich"]
    order = [enrich.index(k) for k in
             ("sec_annual_report", "sec_registration", "sec_proxy", "sec_press_release")]
    assert order == sorted(order)


def test_合併の委任状説明書は読まない():
    """DEFM14A は合併相手の説明を含む（Nordicus Partners に相手のドメインが付いた）。"""
    subs = _submissions(("DEFM14A", "0001-26-2", "m.htm"), ("DEF 14A", "0001-25-1", "d.htm"))
    proxy = latest_proxy("0000000001", subs)
    assert proxy is not None and proxy.form == "DEF 14A"
    assert latest_proxy("0000000001", _submissions(("DEFM14A", "0001-26-2", "m.htm"))) is None


def test_社名を最後に変えた日():
    subs = {"formerNames": [
        {"name": "Nova Lifestyle, Inc.", "from": "2011-06-01T00:00:00.000Z",
         "to": "2025-08-15T00:00:00.000Z"},
        {"name": "Stevens Resources", "from": "2009-01-01T00:00:00.000Z",
         "to": "2011-06-01T00:00:00.000Z"},
    ]}
    assert renamed_at(subs) == "2025-08-15"
    assert renamed_at({"formerNames": []}) is None
    assert renamed_at(None) is None


def test_社名を変える前の届出は読まない(tmp_path):
    """XMax（旧 Nova Lifestyle）に novalifestyle.com が付いた。"""
    old = AnnualReport(cik="0000000001", form="424B5", accession="0001-24-1",
                       document="p.htm", filing_date="2024-03-01")
    new = AnnualReport(cik="0000000001", form="DEF 14A", accession="0001-25-9",
                       document="d.htm", filing_date="2025-10-01")

    class _Client:
        cache_dir = tmp_path

        def get_text(self, url, *, max_bytes):
            return {old.url: "Our website is www.novalifestyle.com.",
                    new.url: "Our website is www.xmax.com."}[url]

    reader = AnnualReportWebsites(_Client())
    assert reader.website_for(old, "XMax Inc.", not_before="2025-08-15") is None
    assert reader.stats.predates_rename == 1
    assert reader.website_for(new, "XMax Inc.", not_before="2025-08-15") == "https://www.xmax.com"


def test_株主名簿管理人は自社サイトにしない():
    for d in ("colonialstock.com", "proxyvote.com", "computershare.com"):
        assert d in THIRD_PARTY


def _reg(n: int) -> AnnualReport:
    return AnnualReport(cik=f"{n:010d}", form="424B4", accession=f"0000-26-9{n:05d}",
                        document="p.htm")


def _annual(n: int) -> AnnualReport:
    return AnnualReport(cik=f"{n:010d}", form="10-K", accession=f"0000-26-1{n:05d}",
                        document="k.htm")


def test_年次報告書が無い会社だけ目論見書で埋める_他社のドメインは採らない(monkeypatch, tmp_path):
    from mailauth.p1_population import sec_edgar, wikidata
    from mailauth.p1_population.sec_edgar import ListedRow, SecFacts

    rows = [
        # 年次報告書で埋まる。目論見書は読まない
        ListedRow(cik="0000000001", name="Old Co", ticker="OLD", exchange="NYSE"),
        # 新規上場。目論見書で埋まる
        ListedRow(cik="0000000002", name="Newly Listed Inc", ticker="NEW", exchange="Nasdaq"),
        # SPAC の合併登録に相手の会社（Old Co）のサイトが書いてある
        ListedRow(cik="0000000003", name="Blank Check Corp", ticker="BCC", exchange="Nasdaq"),
        # どこにも書いていない
        ListedRow(cik="0000000004", name="Quiet Ltd", ticker="QT", exchange="Nasdaq"),
    ]
    pages = {
        _annual(1).url: "Our website address is www.oldco.com.",
        _reg(1).url: "Our website address is www.wrong-for-old.com.",
        _reg(2).url: "Corporate Information. Our website address is www.newlylisted.com.",
        _reg(3).url: "Our website address is www.oldco.com.",
        _reg(4).url: "Nothing here.",
    }
    calls: list[str] = []

    class _Client:
        def __init__(self, *a, **k):
            self.cache_dir = tmp_path / "sec"
            self.stats = sec_edgar.SecStats()

        def get_text(self, url, *, max_bytes):
            calls.append(url)
            return pages.get(url)

        def facts_for_cik(self, cik):
            n = int(cik)
            return SecFacts(cik=cik, annual_report=_annual(n) if n == 1 else None,
                            registration=_reg(n))

        def close(self):
            pass

    monkeypatch.setattr(sec_edgar, "SecEdgarClient", _Client)
    monkeypatch.setattr(sec_edgar, "load_exchange_listing", lambda c, exchanges: (rows, {}))
    monkeypatch.setattr(wikidata, "fetch_cik_identity", lambda q: ({}, {}))
    monkeypatch.setattr(
        wikidata, "fetch_identity", lambda *a, **k: ({}, {"key_count": 0, "unusable_keys": 0})
    )

    result = run_p1(config=CONFIG, run_id=RUN, offline=False)

    from mailauth.io import read_parquet

    df = read_parquet(phase_output(RUN, "p1_population", "entities.parquet")).set_index("cik")
    assert df.loc["0000000001", "official_domain"] == "oldco.com"
    assert df.loc["0000000002", "official_domain"] == "newlylisted.com"
    assert df.loc[["0000000003", "0000000004"], "official_domain"].isna().all()
    assert _reg(1).url not in calls
    stats = result["breakdown"]["sec_registration"]
    assert stats["missing_before"] == 3
    assert stats["shared_dropped"] == 1
    assert stats["still_missing"] == 2
    codes = {w["code"] for w in result["warnings"]}
    assert "SEC_REGISTRATION_SHARED_DOMAIN" in codes


def test_引いていないときはそう書く():
    from mailauth.p1_population.sec_edgar import ListedRow

    rows = [ListedRow(cik="0000320193", name="Apple Inc.", ticker="AAPL", exchange="Nasdaq")]
    result = run_p1(config=CONFIG, run_id=RUN, offline=True, rows=rows)
    codes = {w["code"] for w in result["warnings"]}
    assert "ENRICH_SKIPPED_SEC_REGISTRATION" in codes
