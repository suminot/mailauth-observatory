"""P1 米国母集団（Sprint 1.5）と identity の突合。

**この母集団は Fortune 500 ではない。** 所属リストが CC0 / CC BY-SA の
ソースから500社規模で再構築できないことが実測で分かったため、国内側と同じく
「全上場を測り、絞り込みはビューで行う」形にしてある。

テストは一切ネットワークに出ない。取得層は rows= で差し替える。
"""

from __future__ import annotations

import pytest

from mailauth.contracts import Entity, EntityStatus
from mailauth.io import read_parquet
from mailauth.p1_population import PopulationNotImplementedError
from mailauth.p1_population import run as run_p1
from mailauth.p1_population.identity import (
    WEAK_MATCH_KEY,
    entity_id_for,
    merge_populations,
    promote_to_lei,
)
from mailauth.p1_population.industry import SicMapper
from mailauth.p1_population.sec_edgar import (
    ListedRow,
    SecEdgarError,
    load_exchange_listing,
    user_agent,
)
from mailauth.p1_population.wikidata import (
    WikidataError,
    fetch_cik_identity,
    parse_bindings,
)
from mailauth.paths import phase_output

RUN = "2026-08"
CONFIG = "configs/populations/us-all-listed.yaml"


# ===========================================================================
# SIC の写像
# ===========================================================================


@pytest.mark.parametrize(
    ("sic", "expected"),
    [
        # 4桁の個別則が2桁の一般則より優先されること。
        # 73（Business services）を先に当てるとソフトウェア企業が
        # 軒並みサービス業になる
        ("7372", "10"),
        ("7370", "10"),
        ("7389", "12"),
        ("2834", "3"),
        ("3711", "5"),
        ("6021", "11"),
    ],
)
def test_sic_prefers_the_longer_prefix(sic, expected):
    mapper = SicMapper.load("configs/industry/sic_to_common12.csv")
    common = mapper.map(sic)
    assert common is not None, sic
    assert common.code == expected


def test_sic_counts_unmapped_codes():
    mapper = SicMapper.load("configs/industry/sic_to_common12.csv")
    assert mapper.map(None) is None
    assert mapper.map("abcd") is None
    assert mapper.top_unmapped()


# ===========================================================================
# SEC EDGAR
# ===========================================================================


def test_sec_requires_a_contact_email(monkeypatch):
    """SEC は連絡先を含む User-Agent を必須としている。偽の値は送らない。"""
    monkeypatch.delenv("MAILAUTH_CONTACT_EMAIL", raising=False)
    with pytest.raises(SecEdgarError, match="MAILAUTH_CONTACT_EMAIL"):
        user_agent()


def test_sec_user_agent_includes_the_contact(monkeypatch):
    monkeypatch.setenv("MAILAUTH_CONTACT_EMAIL", "a@example.com")
    assert "a@example.com" in user_agent()


class _FakeSec:
    """company_tickers_exchange.json の応答を差し替える。"""

    def __init__(self, tmp_path, payload):
        self.cache_dir = tmp_path / "sec"
        self._payload = payload

    def get_json(self, url):
        return self._payload


def test_exchange_listing_excludes_otc_and_dedups_cik(tmp_path):
    """OTC は上場企業とは言えないので既定では外す。

    1社が複数クラスの株式を出していると CIK が重複するので潰す。
    """
    payload = {
        "fields": ["cik", "name", "ticker", "exchange"],
        "data": [
            [320193, "Apple Inc.", "AAPL", "Nasdaq"],
            [1652044, "Alphabet Inc.", "GOOGL", "Nasdaq"],
            [1652044, "Alphabet Inc.", "GOOG", "Nasdaq"],  # 同一 CIK
            [999, "Penny Co", "PNNY", "OTC"],
            [888, "No Exchange Co", "NOEX", None],
        ],
    }
    rows, stats = load_exchange_listing(_FakeSec(tmp_path, payload))

    assert [r.cik for r in rows] == ["0000320193", "0001652044"]
    assert stats["duplicate_cik"] == 1
    assert stats["no_exchange"] == 1
    assert stats["by_exchange"]["OTC"] == 1
    assert stats["kept"] == 2


def test_exchange_listing_rejects_an_unexpected_shape(tmp_path):
    with pytest.raises(SecEdgarError, match="列構成"):
        load_exchange_listing(_FakeSec(tmp_path, {"fields": ["x"], "data": []}))


# ===========================================================================
# Wikidata
# ===========================================================================


def test_missing_sparql_file_raises():
    """取得に失敗したら空リストを返さない。「0社だった」と誤解させない。"""
    with pytest.raises(WikidataError, match="見つかりません"):
        parse_bindings([]) and None
        fetch_cik_identity("configs/populations/_sparql/nope.rq")


def test_cik_identity_normalizes_and_counts_conflicts(monkeypatch):
    """CIK の桁揃えを SEC に合わせ、値の競合を数える。"""
    bindings = [
        {"cik": {"value": "320193"}, "website": {"value": "https://apple.com"}},
        {"cik": {"value": "0000320193"}, "website": {"value": "https://www.apple.com"}},
        {"cik": {"value": "789019"}, "lei": {"value": "INR2EJN1ERAN0W5ZP974"}},
        {"cik": {"value": "not-a-number"}},
    ]
    monkeypatch.setattr(
        "mailauth.p1_population.wikidata.run_query", lambda *a, **k: bindings
    )
    identity, stats = fetch_cik_identity(
        "configs/populations/_sparql/us_cik_identity.rq"
    )

    assert identity["0000320193"]["website"] == "https://apple.com"
    assert identity["0000789019"]["lei"] == "INR2EJN1ERAN0W5ZP974"
    assert stats["conflicts"] == 1
    assert stats["cik_count"] == 2


def test_bindings_are_collapsed_per_company():
    bindings = [
        {
            "company": {"value": "http://www.wikidata.org/entity/Q95"},
            "companyLabel": {"value": "Alphabet"},
            "lei": {"value": "5493006MHB84DD0ZWV18"},
        },
        {
            "company": {"value": "http://www.wikidata.org/entity/Q95"},
            "companyLabel": {"value": "Alphabet"},
            "cik": {"value": "1652044"},
        },
        {"company": {"value": "http://www.wikidata.org/entity/Q95"}},
    ]
    rows, stats = parse_bindings(bindings)
    assert len(rows) == 1
    assert rows[0].lei and rows[0].cik
    assert stats["no_name"] == 1


# ===========================================================================
# identity と重複排除
# ===========================================================================


def test_entity_id_prefers_lei():
    assert entity_id_for(lei="ABC", cik="123") == ("lei:ABC", "lei")
    assert entity_id_for(houjin_bangou="1234567890123")[1] == "houjin_bangou"
    assert entity_id_for(cik="0000320193") == ("cik:320193", "cik")
    assert entity_id_for(qid="Q95") == ("wd:Q95", "qid")
    with pytest.raises(ValueError, match="entity_id"):
        entity_id_for()


def _entity(**kwargs) -> Entity:
    base = {
        "entity_id": "x",
        "run_id": RUN,
        "country": "US",
        "population_ids": ["p1"],
        "name": "Example Inc",
        "name_normalized": "exampleinc",
    }
    base.update(kwargs)
    return Entity(**base)


def test_merging_by_lei_does_not_add_a_row():
    """同じ会社が2つの entity になると企業ベースの採用率が二重に数えられる。"""
    a = _entity(entity_id="lei:AAA", lei="AAA", population_ids=["jp-all-listed"])
    b = _entity(entity_id="lei:AAA", lei="AAA", population_ids=["global500"])

    merged, stats = merge_populations([a], [b])
    assert len(merged) == 1
    assert merged[0].population_ids == ["global500", "jp-all-listed"]
    assert stats.by_key == {"lei": 1}
    assert stats.weak_merges == 0


def test_strong_keys_win_over_the_name():
    """LEI で区別できる別会社を商号一致で潰さない。"""
    a = _entity(entity_id="lei:AAA", lei="AAA", name="Acme Inc", name_normalized="acme")
    b = _entity(entity_id="lei:BBB", lei="BBB", name="Acme Inc", name_normalized="acme")

    merged, stats = merge_populations([a], [b])
    assert len(merged) == 2
    assert stats.merged == 0


def test_a_strong_key_on_either_side_blocks_a_name_merge():
    """片方にしか LEI が無くても商号一致では結合しない。

    LEI を持つ会社と持たない会社が同名だからといって同一とは限らない。
    """
    a = _entity(entity_id="lei:AAA", lei="AAA", name_normalized="acme")
    b = _entity(entity_id="wd:Q2", name_normalized="acme", population_ids=["global500"])
    merged, stats = merge_populations([a], [b])
    assert len(merged) == 2
    assert stats.weak_merges == 0


def test_name_only_merges_are_counted_separately():
    """商号一致は最も弱い根拠。件数を出さないと誤マージに気付けない。"""
    a = _entity(entity_id="wd:Q1", name="Acme Inc", name_normalized="acme")
    b = _entity(entity_id="wd:Q2", name="Acme Inc", name_normalized="acme",
                population_ids=["global500"])

    merged, stats = merge_populations([a], [b])
    assert len(merged) == 1
    assert stats.by_key == {WEAK_MATCH_KEY: 1}
    assert stats.weak_merges == 1
    assert stats.weak_samples
    assert "別会社" in " / ".join(stats.notes)


def test_merging_fills_gaps_without_overwriting():
    """先に入っている値を塗り替えない。どちらが正か分からなくなる。"""
    a = _entity(entity_id="lei:AAA", lei="AAA", official_url="https://a.example")
    b = _entity(
        entity_id="lei:AAA", lei="AAA",
        official_url="https://b.example", cik="0000123", population_ids=["global500"],
    )
    merged, _ = merge_populations([a], [b])
    assert merged[0].official_url == "https://a.example"
    assert merged[0].cik == "0000123"


def test_promotion_to_lei_keeps_the_old_id():
    """月次で ID が変わると時系列が切れる。どこから来た ID かを辿れるようにする。"""
    entities = [
        _entity(entity_id="cik:320193", cik="0000320193", lei="HWUPKR0MPOU8FGXBT394"),
        _entity(entity_id="cik:999", cik="0000999"),
    ]
    promoted = promote_to_lei(entities)
    assert promoted == 1
    assert entities[0].entity_id == "lei:HWUPKR0MPOU8FGXBT394"
    assert entities[0].is_duplicate_of == "cik:320193"
    # LEI が無いものは触らない
    assert entities[1].entity_id == "cik:999"


# ===========================================================================
# P1 の通し（米国）
# ===========================================================================


def _rows() -> list[ListedRow]:
    return [
        ListedRow(cik="0000320193", name="Apple Inc.", ticker="AAPL",
                  exchange="Nasdaq", sic="3571", sic_label="Electronic Computers"),
        ListedRow(cik="0000789019", name="MICROSOFT CORP", ticker="MSFT",
                  exchange="Nasdaq", sic="7372", sic_label="Prepackaged Software"),
        ListedRow(cik="0000104169", name="Walmart Inc.", ticker="WMT",
                  exchange="NYSE", sic="5331", sic_label="Variety Stores"),
    ]


def test_us_population_end_to_end():
    result = run_p1(config=CONFIG, run_id=RUN, offline=True, rows=_rows())
    assert result["status"] == "success"

    df = read_parquet(phase_output(RUN, "p1_population", "entities.parquet"))
    assert sorted(df["entity_id"]) == ["cik:104169", "cik:320193", "cik:789019"]
    by_id = df.set_index("entity_id")
    # 4桁の SIC が正しく写っている
    assert by_id.loc["cik:789019", "common12_label"] == "情報通信・IT・メディア"
    assert by_id.loc["cik:104169", "common12_label"] == "商社・卸売・小売"
    assert by_id.loc["cik:320193", "industry_code"] == "3571"


def test_offline_run_says_it_did_not_enrich():
    """「サイトが無い」のではなく「引いていない」を区別する（原則5）。"""
    result = run_p1(config=CONFIG, run_id=RUN, offline=True, rows=_rows())
    codes = {w["code"] for w in result["warnings"]}
    assert "ENRICH_SKIPPED_SEC_SUBMISSIONS" in codes
    assert "ENRICH_SKIPPED_WIKIDATA" in codes

    df = read_parquet(phase_output(RUN, "p1_population", "entities.parquet"))
    assert df["official_url"].isna().all()


def test_offline_without_rows_refuses_to_invent_data():
    with pytest.raises(SecEdgarError, match="offline"):
        run_p1(config=CONFIG, run_id=RUN, offline=True)


def test_fortune500_is_not_implemented_and_says_why():
    """所属リストが CC0 / CC BY-SA から再構築できないことを理由として残す。"""
    with pytest.raises(PopulationNotImplementedError, match="再構築できない"):
        run_p1(config="configs/populations/us-fortune500.yaml", run_id=RUN)
    with pytest.raises(PopulationNotImplementedError, match="再構築できない"):
        run_p1(config="configs/populations/global500.yaml", run_id=RUN)


def test_us_population_is_measured_not_filtered():
    """全上場を測る。Fortune 500 は将来ビューとして重ねる。"""
    from mailauth.config import load_population

    cfg = load_population(CONFIG)
    assert cfg.id == "us-all-listed"
    assert cfg.implemented is True
    assert "SEC EDGAR" in " / ".join(cfg.attribution)
    assert "Wikidata" in " / ".join(cfg.attribution)


def test_delisted_companies_are_marked_not_dropped():
    run_p1(config=CONFIG, run_id="2026-07", offline=True, rows=_rows())
    run_p1(config=CONFIG, run_id=RUN, offline=True, rows=_rows()[:2])

    df = read_parquet(phase_output(RUN, "p1_population", "entities.parquet"))
    by_id = df.set_index("entity_id")
    assert by_id.loc["cik:104169", "status"] == EntityStatus.DELISTED
