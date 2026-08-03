"""ビュー ── 同じ計測結果を別の軸で切り替えて見る。"""

from __future__ import annotations

import pandas as pd
import pytest

from mailauth.contracts import ENTITY_ARROW_SCHEMA
from mailauth.io import records_to_frame
from mailauth.segments import load_segment_map, normalize_segment
from mailauth.views import apply_view, get_view, list_views, load_view, summarize


def _entity(eid: str, *, common12="10", segment=None, status="active", country="JP", pops=None):
    return {
        "entity_id": eid,
        "run_id": "2026-08",
        "country": country,
        "population_ids": pops if pops is not None else ["jp-all-listed"],
        "name": eid,
        "name_normalized": eid,
        "securities_code": eid[-4:],
        "common12_code": common12,
        "common12_label": f"業種{common12}",
        "industry_label": f"一次{common12}",
        "market_segment": segment,
        "status": status,
        "official_domain": f"{eid}.example.jp",
    }


@pytest.fixture
def frame() -> pd.DataFrame:
    rows = [
        _entity("jp:1001", common12="10", segment="prime"),
        _entity("jp:1002", common12="10", segment="prime"),
        _entity("jp:1003", common12="11", segment="standard"),
        _entity("jp:1004", common12="11", segment=None),
        _entity("jp:1005", common12="12", segment="growth"),
        _entity("jp:1006", common12="12", segment="prime", status="delisted"),
    ]
    return records_to_frame(rows, ENTITY_ARROW_SCHEMA)


# -- ビュー定義 --------------------------------------------------------------


def test_shipped_views_load():
    views = {v.id for v in list_views()}
    assert {"jp-all", "jp-prime", "jp-standard", "jp-growth"} <= views


def test_segment_views_declare_their_dependency():
    """区分に依存するビューは requires_segment を立てる。警告を出すため。"""
    assert get_view("jp-prime").requires_segment is True
    assert get_view("jp-all").requires_segment is False


def test_unknown_view_raises():
    with pytest.raises(KeyError):
        get_view("does-not-exist")


def test_missing_view_file_raises():
    with pytest.raises(FileNotFoundError):
        load_view("configs/views/nope.yaml")


# -- 絞り込み ---------------------------------------------------------------


def test_default_view_excludes_delisted(frame):
    """「消えた会社」を現況の分母に入れない。"""
    out = apply_view(frame, get_view("jp-all"))
    assert len(out) == 5
    assert "jp:1006" not in set(out["entity_id"])


def test_prime_view_filters_by_segment(frame):
    out = apply_view(frame, get_view("jp-prime"))
    # delisted の prime は除かれる
    assert set(out["entity_id"]) == {"jp:1001", "jp:1002"}


def test_growth_view(frame):
    assert set(apply_view(frame, get_view("jp-growth"))["entity_id"]) == {"jp:1005"}


# -- 集計 -------------------------------------------------------------------


def test_summarize_by_common12(frame):
    r = summarize(frame, get_view("jp-all"), group_by="common12")
    assert r.total == 5
    assert [(g["code"], g["n"]) for g in r.groups] == [("10", 2), ("11", 2), ("12", 1)]
    assert abs(sum(g["share"] for g in r.groups) - 1.0) < 1e-6


def test_summarize_by_segment_shows_unclassified(frame):
    """区分が付いていない企業を黙って消さない。(未分類) として出す。"""
    r = summarize(frame, get_view("jp-all"), group_by="segment")
    by_code = {g["code"]: g["n"] for g in r.groups}
    assert by_code["prime"] == 2
    assert by_code["(未分類)"] == 1
    assert r.total == 5


def test_prime_view_narrows_the_industry_breakdown(frame):
    """同じ計測結果から、プライムだけの業種構成が出せること。"""
    r = summarize(frame, get_view("jp-prime"), group_by="common12")
    assert r.total == 2
    assert [(g["code"], g["n"]) for g in r.groups] == [("10", 2)]


def test_coverage_is_reported(frame):
    r = summarize(frame, get_view("jp-prime"))
    assert r.coverage["entities_in_run"] == 6
    assert r.coverage["entities_in_view"] == 2
    assert r.coverage["with_segment"] == 2


def test_unknown_axis_is_rejected(frame):
    with pytest.raises(ValueError, match="不明な集計軸"):
        summarize(frame, get_view("jp-all"), group_by="bogus")


# -- 原則5 「該当なし」と「データが無い」を区別する ----------------------------


def test_prime_view_warns_when_segment_data_is_absent():
    """区分データが無いときの総数0を「該当企業なし」と誤読させない。"""
    rows = [_entity("jp:2001", segment=None), _entity("jp:2002", segment=None)]
    df = records_to_frame(rows, ENTITY_ARROW_SCHEMA)

    r = summarize(df, get_view("jp-prime"))
    assert r.total == 0
    assert r.segment_data_available is False
    assert r.warnings, "区分データが無いことを警告していない"
    assert "該当企業が無いからではなく" in r.warnings[0]


def test_no_warning_when_segment_data_is_present(frame):
    r = summarize(frame, get_view("jp-prime"))
    assert r.segment_data_available is True
    assert r.warnings == []


def test_all_view_never_warns_about_segments():
    rows = [_entity("jp:3001", segment=None)]
    df = records_to_frame(rows, ENTITY_ARROW_SCHEMA)
    assert summarize(df, get_view("jp-all")).warnings == []


# -- 市場区分の対応表 ---------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("プライム", "prime"),
        ("プライム市場", "prime"),
        ("Prime", "prime"),
        ("スタンダード", "standard"),
        ("グロース", "growth"),
        ("", None),
        (None, None),
    ],
)
def test_normalize_segment(raw, expected):
    assert normalize_segment(raw) == expected


def test_load_segment_map(tmp_path):
    csv_path = tmp_path / "seg.csv"
    csv_path.write_text(
        "securities_code,market_segment,source,retrieved\n"
        "7203,プライム,有価証券報告書,2026-08-01\n"
        "72040,スタンダード,有価証券報告書,2026-08-01\n"  # EDINET の5桁も通す
        ",プライム,,\n"  # 証券コードなし -> invalid
        "9999,,,\n",  # 区分なし -> invalid
        encoding="utf-8",
    )
    m = load_segment_map(csv_path)
    assert len(m) == 2
    assert m.get("7203") == "prime"
    assert m.get("7204") == "standard"
    assert m.get("72040") == "standard"  # 5桁で引いても正規化される
    assert m.get("0000") is None
    assert m.invalid_rows == 2
    assert m.source == "有価証券報告書"
    assert m.retrieved == "2026-08-01"
    assert m.counts() == {"prime": 1, "standard": 1}


def test_segment_map_requires_securities_code_column(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("code,segment\n7203,prime\n", encoding="utf-8")
    with pytest.raises(ValueError, match="securities_code"):
        load_segment_map(bad)


def test_missing_segment_map_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_segment_map(tmp_path / "nope.csv")
