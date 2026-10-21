"""プレスリリースから公式サイトを取る（BACKLOG 15）。

年次報告書で取れなかった米国企業向け。**社名と合うドメインだけを採る**
── 問い合わせ先は IR 代行会社のことが多いため。

テストは一切ネットワークに出ない。取得層は差し替える。
"""

from __future__ import annotations

import json

from mailauth.p1_population import run as run_p1
from mailauth.p1_population.press_release import (
    EXTRACTOR_VERSION,
    PressReleaseWebsites,
    recent_releases,
    release_domains,
)
from mailauth.paths import phase_output

RUN = "2026-08"
CONFIG = "configs/populations/us-all-listed.yaml"

RELEASE = """
<p>Acme Robotics Reports Third Quarter Results</p>
<p>Revenue grew 12% about the quarter compared with www.notabout.com.</p>
<p><b>About Acme Robotics</b></p>
<p>Acme Robotics builds warehouse robots. For more information, visit www.acmerobotics.com.</p>
<p>Investor Contact: Jane Doe, jane.doe@acmerobotics.com</p>
<p>Media Contact: John Roe, john@lifesciadvisors.com</p>
<p>Distributed by www.businesswire.com</p>
"""


# ===========================================================================
# 本文から読む
# ===========================================================================


def test_会社紹介と問い合わせ先のドメインを数える():
    found = release_domains(RELEASE)
    assert found["acmerobotics.com"] == 2  # About の直後とメール
    # IR 代行のドメインも候補には入る。採るかどうかは社名で決める
    assert found["lifesciadvisors.com"] == 1
    # 配信会社は会社のものではない
    assert "businesswire.com" not in found


def test_小文字の_about_は見出しとして読まない():
    """「about the quarter」の後ろのドメインを会社紹介と取り違えない。"""
    assert "notabout.com" not in release_domains(RELEASE)


def test_個人向けメールのドメインは数えない():
    found = release_domains("<p>Contact: ceo.smith@gmail.com or ir@qq.com</p>")
    assert dict(found) == {}


def test_新しい順に_8K_と_6K_を3件まで():
    subs = {
        "filings": {
            "recent": {
                "form": ["10-Q", "8-K", "S-8", "6-K", "8-K", "8-K"],
                "accessionNumber": ["a", "b", "c", "d", "e", "f"],
            }
        }
    }
    assert recent_releases(subs) == ["b", "d", "e"]
    assert recent_releases(None) == []


# ===========================================================================
# 取得とキャッシュ
# ===========================================================================


class _FakeClient:
    """index.json と添付を返す。"""

    def __init__(self, tmp_path, releases: dict[str, str | Exception]):
        self.cache_dir = tmp_path / "sec"
        self.releases = releases
        self.calls: list[str] = []

    def get_json(self, url):
        self.calls.append(url)
        accession = url.rstrip("/").split("/")[-2]
        page = self.releases.get(accession)
        if isinstance(page, Exception):
            raise page
        return {"directory": {"item": [{"name": "form8-k.htm"}, {"name": "ex99-1.htm"}]}}

    def get_text(self, url, *, max_bytes):
        self.calls.append(url)
        return self.releases[url.split("/")[-2]]


def test_社名と合うドメインだけを採る(tmp_path):
    """**問い合わせ先の IR 代行を自社のものにしない。**"""
    client = _FakeClient(tmp_path, {"000111": RELEASE})
    reader = PressReleaseWebsites(client)
    assert reader.website_for("0000000001", ["0001-1-1"], "Acme Robotics Inc") == (
        "https://acmerobotics.com"
    )
    assert reader.stats.found == 1


def test_社名と合わなければ採らない(tmp_path):
    release = "<p>About Zeta Holdings</p><p>Contact: ir@lifesciadvisors.com</p>"
    client = _FakeClient(tmp_path, {"000111": release})
    reader = PressReleaseWebsites(client)
    assert reader.website_for("0000000001", ["0001-1-1"], "Zeta Holdings Inc") is None
    assert reader.stats.name_mismatch == 1


def test_メールアドレスはキャッシュに残さない(tmp_path):
    """**個人名を含むメールアドレスを保存しない**（ドメイン部だけ）。"""
    client = _FakeClient(tmp_path, {"000111": RELEASE})
    PressReleaseWebsites(client).website_for("0000000001", ["0001-1-1"], "Acme Robotics")
    cached = (client.cache_dir / "press_release" / "0001-1-1.json").read_text()
    assert "@" not in cached
    assert "jane" not in cached.lower()
    assert json.loads(cached)["extractor_version"] == EXTRACTOR_VERSION


def test_提出済みの書類は二度落とさない(tmp_path):
    client = _FakeClient(tmp_path, {"000111": RELEASE})
    PressReleaseWebsites(client).website_for("0000000001", ["0001-1-1"], "Acme Robotics")
    calls = len(client.calls)
    again = PressReleaseWebsites(client)
    assert again.website_for("0000000001", ["0001-1-1"], "Acme Robotics") == (
        "https://acmerobotics.com"
    )
    assert len(client.calls) == calls
    assert again.stats.cache_hit == 1


def test_読めなかったのと書いていなかったのを分ける(tmp_path):
    """原則5。読めなかったものは持ち越さない ── 次の実行で読み直す。"""
    client = _FakeClient(
        tmp_path, {"000111": RuntimeError("503"), "000222": "<p>Quarterly results.</p>"}
    )
    reader = PressReleaseWebsites(client)
    assert reader.website_for("0000000001", ["0001-1-1"], "Acme") is None
    assert reader.website_for("0000000002", ["0002-2-2"], "Beta") is None
    assert reader.website_for("0000000003", [], "Gamma") is None
    assert (reader.stats.unreadable, reader.stats.nothing_found, reader.stats.no_releases) == (
        1,
        1,
        1,
    )
    assert not (client.cache_dir / "press_release" / "0001-1-1.json").exists()


# ===========================================================================
# P1 の米国母集団に入っていること
# ===========================================================================


def test_米国母集団がプレスリリースを読む設定になっている():
    import yaml

    data = yaml.safe_load(open(CONFIG, encoding="utf-8"))
    enrich = data["source"]["enrich"]
    # **年次報告書の後。** 年次報告書は会社が「当社のサイト」と書いた箇所で、
    # こちらより強い根拠だから
    assert enrich.index("sec_press_release") > enrich.index("sec_annual_report")


def test_年次報告書で埋まらなかった企業だけをプレスリリースで埋める(monkeypatch, tmp_path):
    from mailauth.p1_population import sec_edgar, wikidata
    from mailauth.p1_population.annual_report import AnnualReport
    from mailauth.p1_population.sec_edgar import ListedRow, SecFacts

    rows = [
        ListedRow(cik="0000000001", name="Annual Co", ticker="ANN", exchange="NYSE"),
        ListedRow(cik="0000000002", name="Acme Robotics Inc", ticker="ACME", exchange="NYSE"),
    ]
    annual = AnnualReport(cik="0000000001", form="10-K", accession="0001-26-1", document="k.htm")

    class _Client(_FakeClient):
        def __init__(self, *a, **k):
            super().__init__(tmp_path, {"000111": RELEASE, "000222": RELEASE})
            self.stats = sec_edgar.SecStats()

        def facts_for_cik(self, cik):
            if cik == "0000000001":
                return SecFacts(cik=cik, annual_report=annual, releases=["0001-1-1"])
            return SecFacts(cik=cik, releases=["0002-2-2"])

        def get_text(self, url, *, max_bytes):
            self.calls.append(url)
            if url.endswith("k.htm"):
                return "Our website is www.annualco.com."
            return super().get_text(url, max_bytes=max_bytes)

        def close(self):
            pass

    holder: dict = {}

    def _make(*a, **k):
        holder["c"] = _Client()
        return holder["c"]

    monkeypatch.setattr(sec_edgar, "SecEdgarClient", _make)
    monkeypatch.setattr(sec_edgar, "load_exchange_listing", lambda c, exchanges: (rows, {}))
    monkeypatch.setattr(wikidata, "fetch_cik_identity", lambda q: ({}, {}))
    monkeypatch.setattr(
        wikidata, "fetch_identity", lambda *a, **k: ({}, {"key_count": 0, "unusable_keys": 0})
    )

    result = run_p1(config=CONFIG, run_id=RUN, offline=False)

    from mailauth.io import read_parquet

    df = read_parquet(phase_output(RUN, "p1_population", "entities.parquet")).set_index("cik")
    assert df.loc["0000000001", "official_domain"] == "annualco.com"
    assert df.loc["0000000002", "official_domain"] == "acmerobotics.com"
    # 年次報告書で埋まった会社のプレスリリースは読んでいない
    assert not any("000111" in c for c in holder["c"].calls)
    stats = result["breakdown"]["sec_press_release"]
    assert (stats["missing_before"], stats["found"], stats["still_missing"]) == (1, 1, 0)


def test_引いていないときはそう書く():
    from mailauth.p1_population.sec_edgar import ListedRow

    rows = [ListedRow(cik="0000320193", name="Apple Inc.", ticker="AAPL", exchange="Nasdaq")]
    result = run_p1(config=CONFIG, run_id=RUN, offline=True, rows=rows)
    assert "ENRICH_SKIPPED_SEC_PRESS_RELEASE" in {w["code"] for w in result["warnings"]}
