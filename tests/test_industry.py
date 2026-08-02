"""業種の写像。EDINET33 -> 共通12分類（DR-12）。"""

from __future__ import annotations

import pytest

from mailauth.p1_population.industry import IndustryMapper

#: EDINETコードリストの「提出者業種」列が取りうる33のラベル（DR-12）。
#: 実データではこのうち「倉庫・運輸関連」だけ末尾の「業」が無い。
EDINET33_LABELS = [
    "水産・農林業", "鉱業", "建設業", "食料品", "繊維製品", "パルプ・紙", "化学",
    "医薬品", "石油・石炭製品", "ゴム製品", "ガラス・土石製品", "鉄鋼", "非鉄金属",
    "金属製品", "機械", "電気機器", "輸送用機器", "精密機器", "その他製品",
    "電気・ガス業", "陸運業", "海運業", "空運業", "倉庫・運輸関連", "情報・通信業",
    "卸売業", "小売業", "銀行業", "証券、商品先物取引業", "保険業", "その他金融業",
    "不動産業", "サービス業",
]

MAPPING = "configs/industry/edinet33_to_common12.csv"


@pytest.fixture
def mapper() -> IndustryMapper:
    return IndustryMapper.load(MAPPING)


def test_all_33_edinet_labels_are_mapped(mapper):
    """1つでも写せないと業種別集計に穴が空く。33業種すべてを網羅すること。"""
    missing = [label for label in EDINET33_LABELS if mapper.map(label) is None]
    assert missing == [], f"共通12分類に写せないラベル: {missing}"


def test_mapped_codes_are_within_1_to_12(mapper):
    for label in EDINET33_LABELS:
        common = mapper.map(label)
        assert common is not None
        assert common.code in {str(i) for i in range(1, 13)}
        assert common.label


def test_all_12_classes_are_reachable(mapper):
    """12分類のどれかが誰にも使われないなら、写像かクラス定義が間違っている。"""
    used = {mapper.map(label).code for label in EDINET33_LABELS}
    assert used == {str(i) for i in range(1, 13)}


@pytest.mark.parametrize(
    ("label", "code"),
    [
        ("情報・通信業", "10"),
        ("銀行業", "11"),
        ("証券、商品先物取引業", "11"),  # ラベルに読点を含む。CSV の引用符が効いているか
        ("輸送用機器", "5"),
        ("卸売業", "8"),
        ("倉庫・運輸関連", "9"),
        ("倉庫・運輸関連業", "9"),  # JPX 表記のゆれも吸収する
        ("サービス業", "12"),
    ],
)
def test_specific_mappings(mapper, label, code):
    assert mapper.map(label).code == code


def test_unknown_label_is_counted_not_raised(mapper):
    """未知ラベルで落とさない。数えて manifest に出し、写像CSVを育てる材料にする。"""
    assert mapper.map("宇宙開発業") is None
    assert mapper.map("宇宙開発業") is None
    assert mapper.unmapped["宇宙開発業"] == 2
    assert mapper.top_unmapped(1) == [("宇宙開発業", 2)]


def test_none_label_is_not_recorded_as_unmapped(mapper):
    assert mapper.map(None) is None
    assert mapper.unmapped == {}


def test_map_version_is_single_valued(mapper):
    assert mapper.map_version
    assert mapper.map("化学").map_version == mapper.map_version


def test_mixed_map_version_is_rejected(tmp_path):
    """写像を改定したら全行の version を揃える。混在は時系列比較を壊す。"""
    csv_path = tmp_path / "bad.csv"
    csv_path.write_text(
        "scheme,src_code,src_label,common_code,common_label,map_version,note\n"
        "EDINET33,,化学,3,素材・化学,2026-08-01,\n"
        "EDINET33,,機械,4,機械・電機・精密,2026-09-01,\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="map_version"):
        IndustryMapper.load(csv_path)


def test_sic_mapping_loads():
    """Sprint 1.5 で使う SIC 写像も、今のうちに読めることだけ確かめる。"""
    m = IndustryMapper.load("configs/industry/sic_to_common12.csv")
    assert m.map_version
