"""事業会社でないもの（SPAC・ファンド・ETF）を母集団から外す。

**運営者の判断（2026-10-21）: 外して、外した会社は名前と理由を示す。**
判定は SEC 自身の区分で行い、社名の語だけでは決めない。

テストは一切ネットワークに出ない。
"""

from __future__ import annotations

import pytest

from mailauth.p1_population import run as run_p1
from mailauth.p1_population.non_operating import (
    COMMODITY_TRUST,
    PASSIVE_TRUST,
    REGISTERED_FUND,
    SPAC,
    classify,
)
from mailauth.paths import phase_output

RUN = "2026-08"
CONFIG = "configs/populations/us-all-listed.yaml"


@pytest.mark.parametrize(
    ("sic", "forms", "name", "expected"),
    [
        # SPAC は SEC が業種コード 6770 を付ける
        ("6770", {"10-K", "8-K"}, "Churchill Capital Corp XII", SPAC),
        # 商品・暗号資産の上場信託
        ("6221", {"10-K", "10-Q"}, "iShares Bitcoin Trust ETF", COMMODITY_TRUST),
        ("6221", {"10-K"}, "United States Oil Fund, LP", COMMODITY_TRUST),
        # **6221 でも事業会社は残す**（2026-10-21 の全数で実在した）
        ("6221", {"10-K"}, "Uranium Royalty Corp.", None),
        ("6221", {"10-K"}, "AI Financial Corp", None),
        # 登録投資会社（投資信託・ETF・クローズドエンド）
        (
            None,
            {"N-CSR", "NPORT-P", "DEF 14A"},
            "Nuveen Arizona Quality Municipal Income Fund",
            REGISTERED_FUND,
        ),
        (None, {"485BPOS", "N-30D", "497"}, "SPDR S&P 500 ETF TRUST", REGISTERED_FUND),
        (None, {"N-2", "N-2/A"}, "Pershing Square USA, Ltd.", REGISTERED_FUND),
        # **BDC は 10-K を出す会社なので残す**（N-2 を出していても）
        (None, {"N-2", "10-K", "10-Q", "8-K"}, "Goldman Sachs BDC, Inc.", None),
        # 事業会社
        ("3571", {"10-K", "10-Q", "8-K"}, "Apple Inc.", None),
        # **社名だけでは決めない。** 「Trust」は銀行にも付く
        ("6022", {"10-K", "10-Q"}, "Northern Trust Corp", None),
        # 「Acquisition Holdings」は事業会社にもあるので残す
        ("7372", {"10-K"}, "Example Acquisition Holdings Inc", None),
        # **SEC が買収予定先の業種コードを付けた SPAC**（2026-10-21 の全数で 23 社）
        ("7372", {"10-K", "S-1"}, "McKinley Acquisition Corp", SPAC),
        ("4911", {"10-K"}, "Plum Acquisition Corp. IV", SPAC),
        # ロイヤルティ信託・仕組み債の信託
        ("6792", {"10-K"}, "SAN JUAN BASIN ROYALTY TRUST", PASSIVE_TRUST),
        ("6189", {"10-K"}, "STRATS(SM) Trust for Procter & Gamble Securities", PASSIVE_TRUST),
        # **6792 でも事業会社は残す**
        ("6792", {"10-K", "10-Q"}, "Texas Pacific Land Corp", None),
        ("6792", {"10-K"}, "LandBridge Corp", None),
        # 表から読んだ欠損（NaN）は「業種コードなし」として扱う
        (float("nan"), {"10-K"}, "Apple Inc.", None),
    ],
)
def test_事業会社でないものの判定(sic, forms, name, expected):
    assert classify(sic, forms, name) == expected


def test_外した会社を名前と理由つきで残し_母集団からは除く(monkeypatch, tmp_path):
    from mailauth.io import read_parquet
    from mailauth.p1_population import sec_edgar, wikidata
    from mailauth.p1_population.sec_edgar import ListedRow, SecFacts

    rows = [
        ListedRow(cik="0000000001", name="Apple Inc.", ticker="AAPL", exchange="Nasdaq"),
        ListedRow(
            cik="0000000002", name="Churchill Capital Corp XII", ticker="CCXII", exchange="Nasdaq"
        ),
        ListedRow(cik="0000000003", name="Nuveen Muni Fund", ticker="NUV", exchange="NYSE"),
    ]
    facts = {
        "0000000001": SecFacts(cik="0000000001", sic="3571", forms=frozenset({"10-K"})),
        "0000000002": SecFacts(cik="0000000002", sic="6770", forms=frozenset({"10-K"})),
        "0000000003": SecFacts(cik="0000000003", forms=frozenset({"N-CSR", "NPORT-P"})),
    }

    class _Client:
        cache_dir = tmp_path / "sec"

        def __init__(self, *a, **k):
            self.stats = sec_edgar.SecStats()

        def facts_for_cik(self, cik):
            return facts[cik]

        def get_text(self, url, *, max_bytes):
            return ""

        def get_json(self, url):
            return {}

        def close(self):
            pass

    monkeypatch.setattr(sec_edgar, "SecEdgarClient", _Client)
    monkeypatch.setattr(sec_edgar, "load_exchange_listing", lambda c, exchanges: (rows, {}))
    monkeypatch.setattr(wikidata, "fetch_cik_identity", lambda q: ({}, {}))
    monkeypatch.setattr(
        wikidata, "fetch_identity", lambda *a, **k: ({}, {"key_count": 0, "unusable_keys": 0})
    )

    result = run_p1(config=CONFIG, run_id=RUN, offline=False)

    entities = read_parquet(phase_output(RUN, "p1_population", "entities.parquet"))
    assert list(entities["cik"]) == ["0000000001"]

    excluded = read_parquet(phase_output(RUN, "p1_population", "excluded_entities.parquet"))
    assert sorted(zip(excluded["name"], excluded["reason"], strict=True)) == [
        ("Churchill Capital Corp XII", SPAC),
        ("Nuveen Muni Fund", REGISTERED_FUND),
    ]
    assert set(excluded["population_id"]) == {"us-all-listed"}
    assert result["breakdown"]["non_operating_excluded"]["total"] == 2
    assert "NON_OPERATING_EXCLUDED" in {w["code"] for w in result["warnings"]}


def test_判定できないときはそう書く():
    """届出の様式が無いと判定できない。**判定しなかったことを黙らない**（原則5）。"""
    from mailauth.p1_population.sec_edgar import ListedRow

    rows = [ListedRow(cik="0000320193", name="Apple Inc.", ticker="AAPL", exchange="Nasdaq")]
    result = run_p1(config=CONFIG, run_id=RUN, offline=True, rows=rows)
    assert "NON_OPERATING_NOT_CLASSIFIED" in {w["code"] for w in result["warnings"]}


def test_外した会社を上場廃止として記録しない(monkeypatch, tmp_path):
    """前月の母集団にいた会社を外しても、**「上場廃止」ではない。**"""
    from mailauth.contracts import ENTITY_ARROW_SCHEMA, Entity, EntityStatus
    from mailauth.io import records_to_frame, write_parquet
    from mailauth.manifest import RunManifest
    from mailauth.p1_population.runner import _diff_with_previous

    prev = [
        Entity(
            entity_id="cik:1",
            run_id="2026-07",
            country="US",
            name="Apple Inc.",
            name_normalized="apple",
            cik="0000000001",
            status=EntityStatus.ACTIVE,
        ),
        Entity(
            entity_id="cik:2",
            run_id="2026-07",
            country="US",
            name="Nuveen Muni Fund",
            name_normalized="nuveenmuni",
            cik="0000000002",
            status=EntityStatus.ACTIVE,
        ),
    ]
    write_parquet(
        records_to_frame(prev, ENTITY_ARROW_SCHEMA),
        phase_output("2026-07", "p1_population", "entities.parquet"),
        ENTITY_ARROW_SCHEMA,
    )
    current = [prev[0].model_copy(update={"run_id": RUN})]
    manifest = RunManifest(phase="p1_population", run_id=RUN, out_dir=tmp_path)
    out = _diff_with_previous(current, RUN, manifest, excluded_ciks={"0000000002"})
    assert [e.entity_id for e in out] == ["cik:1"]
    assert manifest.to_dict()["breakdown"]["diff"]["delisted_entities"] == 0
