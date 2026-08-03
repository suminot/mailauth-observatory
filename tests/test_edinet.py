"""EDINETコードリストの取得と解析。"""

from __future__ import annotations

import pytest

from mailauth.config import load_population
from mailauth.p1_population.edinet import EdinetError, fetch_code_list, parse_code_list


@pytest.fixture
def cfg():
    return load_population("configs/populations/jp-all-listed.yaml").source.edinet_code_list


def test_parses_cp932_csv(edinet_sample, cfg):
    rows, stats = parse_code_list(edinet_sample, cfg)
    assert stats["skipped_header"] == 2  # タイトル行 + ヘッダ行
    assert stats["skipped_malformed"] == 1  # 列数不足の行
    assert len(rows) == 20
    first = rows[0]
    assert first.edinet_code == "E00001"
    assert first.name == "株式会社サンプル情報システム"
    assert first.industry == "情報・通信業"
    assert first.securities_code == "12340"
    assert first.houjin_bangou == "1234567890123"


def test_parses_zip_the_same_as_csv(edinet_sample, edinet_sample_zip, cfg):
    """配信形式が ZIP でも中身の CSV でも、同じ結果になること。"""
    from_csv, _ = parse_code_list(edinet_sample, cfg)
    from_zip, _ = parse_code_list(edinet_sample_zip, cfg)
    assert [r.edinet_code for r in from_csv] == [r.edinet_code for r in from_zip]


def test_column_positions_come_from_config(edinet_sample, cfg):
    """列位置は設定から取る。EDINET の列構成が変わってもコードを触らずに追随する。"""
    cfg.columns = {**cfg.columns, "name": 8}  # 提出者名の位置をヨミの列にずらす
    rows, _ = parse_code_list(edinet_sample, cfg)
    assert rows[0].name == "サンプルジョウホウシステム"


def test_missing_required_column_config_is_rejected(edinet_sample, cfg):
    cfg.columns = {"edinet_code": 0}
    with pytest.raises(EdinetError, match="列位置の設定が足りません"):
        parse_code_list(edinet_sample, cfg)


def test_source_file_bypasses_network(edinet_sample, cfg):
    """--source-file が指定されたらネットワークに出ない。テストが外部に依存しない前提。"""
    result = fetch_code_list(cfg, source_file=edinet_sample)
    assert result.path == edinet_sample
    assert result.from_cache is True


def test_missing_source_file_raises(cfg, tmp_path):
    with pytest.raises(EdinetError, match="指定されたソースファイル"):
        fetch_code_list(cfg, source_file=tmp_path / "nope.csv")
