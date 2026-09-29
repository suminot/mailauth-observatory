"""一意でない鍵（ティッカー・証券コード）での突合。

**取り逃がしと誤った突合は、悪さの桁が違う。** 突合できなければその企業が
分母から落ちるだけだが、誤って突合すると**別の会社の測定値をその企業の
ものとして出す。** 検証の主眼はそこにある。
"""

from __future__ import annotations

import pytest

from mailauth.p1_population import wikidata as wd
from mailauth.p1_population.wikidata import (
    IDENTITY_KEYS,
    fetch_identity,
    names_agree,
)


def _b(**kw) -> dict:
    return {k: {"value": v} for k, v in kw.items()}


@pytest.fixture
def stub(monkeypatch):
    def _install(bindings):
        monkeypatch.setattr(wd, "load_query", lambda p: "SELECT 1")
        monkeypatch.setattr(wd, "run_query", lambda *a, **k: bindings)

    return _install


# ---------------------------------------------------------------------------
# 鍵の正規化
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [(" aapl ", "AAPL"), ("BRK.B", "BRK.B"), ("", None), ("  ", None)],
)
def test_ticker_is_normalised(raw, expected):
    assert IDENTITY_KEYS["ticker"](raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("7974", "7974"),
        ("TSE:7974", "7974"),
        # **4桁でないものは捨てる。** P249 には海外市場のティッカーも入る
        ("AAPL", None),
        ("79741", None),
        ("797", None),
    ],
)
def test_a_securities_code_must_be_four_digits(raw, expected):
    assert IDENTITY_KEYS["securities_code"](raw) == expected


# ---------------------------------------------------------------------------
# 歯止め1: 衝突したら捨てる
# ---------------------------------------------------------------------------


def test_an_ambiguous_key_is_dropped_not_guessed(stub):
    """**どちらの会社か分からないなら、両方使わない。**

    最初のものを採ると、別の会社のサイトをその企業に紐づける。
    """
    stub(
        [
            _b(ticker="AAPL", website="https://apple.example"),
            _b(ticker="ZZZZ", website="https://one.example"),
            _b(ticker="ZZZZ", website="https://two.example"),
        ]
    )
    out, stats = fetch_identity("q", key="ticker", drop_on_conflict=True)
    assert "ZZZZ" not in out
    assert out["AAPL"]["website"] == "https://apple.example"
    assert stats["dropped_ambiguous"] == 1


def test_a_strong_key_still_keeps_the_first(stub):
    """CIK は一意なので、競合は向こうのデータ誤り。**捨てない。**"""
    stub(
        [
            _b(cik="320193", website="https://a.example"),
            _b(cik="320193", website="https://b.example"),
        ]
    )
    out, stats = fetch_identity("q", key="cik")
    assert out["0000320193"]["website"] == "https://a.example"
    assert stats["conflicts"] == 1
    assert stats["dropped_ambiguous"] == 0


# ---------------------------------------------------------------------------
# 歯止め2: 社名が合わなければ使わない
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ours,theirs",
    [
        ("Alphabet Inc.", "Alphabet"),
        ("Kubota Corporation", "Kubota"),
        ("株式会社クボタ", "クボタ"),
        ("Sony Group Corporation", "Sony Group"),
    ],
)
def test_the_same_company_written_differently_agrees(ours, theirs):
    assert names_agree(ours, theirs)


@pytest.mark.parametrize(
    "ours,theirs",
    [
        ("Apple Inc.", "Apricot Inc."),
        ("Meta Platforms", "Meta Materials"),
        # **片方が無ければ判断できない。** 通さない
        (None, "Apple"),
        ("Apple", None),
        ("Apple", ""),
        # 法人格だけでは同じと言えない
        ("株式会社", "株式会社"),
    ],
)
def test_different_companies_do_not_agree(ours, theirs):
    assert not names_agree(ours, theirs)


# ---------------------------------------------------------------------------
# 埋める側
# ---------------------------------------------------------------------------


class _Entity:
    def __init__(self, name, ticker, domain=None):
        self.name = name
        self.name_en = name
        self.ticker = ticker
        self.official_url = None
        self.official_domain = domain
        self.lei = None


class _Manifest:
    def __init__(self):
        self.warnings = []

    def add_warning(self, code, **kw):
        self.warnings.append((code, kw))


def _fill(entities, bindings, monkeypatch):
    from mailauth.p1_population import runner

    monkeypatch.setattr(wd, "load_query", lambda p: "SELECT 1")
    monkeypatch.setattr(wd, "run_query", lambda *a, **k: bindings)
    manifest = _Manifest()
    stats = runner._fill_from_weak_key(
        entities,
        query_path="q",
        key="ticker",
        key_of=lambda e: e.ticker,
        name_of=lambda e: e.name_en,
        manifest=manifest,
        label="ticker",
    )
    return stats, manifest


def test_a_matching_ticker_and_name_fills_the_domain(monkeypatch):
    e = _Entity("Kubota Corporation", "KUBTY")
    stats, _ = _fill(
        [e],
        [_b(ticker="KUBTY", website="https://kubota.example/ir", label="Kubota")],
        monkeypatch,
    )
    assert e.official_domain == "kubota.example"
    assert stats["filled"] == 1


def test_a_matching_ticker_with_a_different_name_is_refused(monkeypatch):
    """**ここが一番怖い経路。** 鍵が当たっただけでは紐づけない。"""
    e = _Entity("Apple Inc.", "AAPL")
    stats, manifest = _fill(
        [e],
        [_b(ticker="AAPL", website="https://apricot.example", label="Apricot Inc.")],
        monkeypatch,
    )
    assert e.official_domain is None
    assert stats["filled"] == 0
    assert stats["name_rejected"] == 1
    assert any(c == "WIKIDATA_NAME_MISMATCH" for c, _ in manifest.warnings)


def test_an_entity_that_already_has_a_domain_is_left_alone(monkeypatch):
    """**上書きしない。** 一次情報で取れているものを弱い鍵で塗り替えない。"""
    e = _Entity("Kubota Corporation", "KUBTY", domain="kubota.co.jp")
    stats, _ = _fill(
        [e],
        [_b(ticker="KUBTY", website="https://elsewhere.example", label="Kubota")],
        monkeypatch,
    )
    assert e.official_domain == "kubota.co.jp"
    assert stats["missing_before"] == 0


def test_an_ambiguous_ticker_fills_nothing(monkeypatch):
    e = _Entity("Zeta Corporation", "ZZZZ")
    stats, _ = _fill(
        [e],
        [
            _b(ticker="ZZZZ", website="https://one.example", label="Zeta Corporation"),
            _b(ticker="ZZZZ", website="https://two.example", label="Zeta Corporation"),
        ],
        monkeypatch,
    )
    assert e.official_domain is None
    assert stats["dropped_ambiguous"] == 1


def test_entities_without_a_key_are_counted_not_hidden(monkeypatch):
    """**「鍵が無い」と「当たらなかった」は別である**（原則5）。"""
    e = _Entity("No Ticker Corporation", None)
    stats, _ = _fill([e], [], monkeypatch)
    assert stats["without_key"] == 1
