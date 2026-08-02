"""テスト共通のフィクスチャ。

テストは一切ネットワークに出ない。EDINET / gBizINFO / 国税庁のいずれも、
ローカルのフィクスチャか未設定スキップで賄えるようにしてある。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
EDINET_SAMPLE = FIXTURES / "EdinetcodeDlInfo_sample.csv"


@pytest.fixture(autouse=True)
def isolated_data_root(tmp_path, monkeypatch):
    """data/ をテストごとに使い捨てにする。実行結果が互いに干渉しないように。"""
    monkeypatch.setenv("MAILAUTH_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("MAILAUTH_GOLD_ROOT", str(tmp_path / "gold"))
    # 認証情報が開発機に残っていてもテストでは使わない
    for key in (
        "MAILAUTH_EDINET_SUBSCRIPTION_KEY",
        "MAILAUTH_GBIZINFO_TOKEN",
        "MAILAUTH_HOUJIN_BANGOU_APP_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    yield tmp_path


@pytest.fixture
def edinet_sample() -> Path:
    assert EDINET_SAMPLE.is_file(), "EDINET のフィクスチャが見つかりません"
    return EDINET_SAMPLE


@pytest.fixture
def edinet_sample_zip(tmp_path, edinet_sample) -> Path:
    """フィクスチャ CSV を ZIP に固めたもの。ZIP 経路の検証に使う。"""
    import zipfile

    path = tmp_path / "Edinetcode.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("EdinetcodeDlInfo.csv", edinet_sample.read_bytes())
    return path


@pytest.fixture
def jp_config() -> str:
    return "configs/populations/jp-all-listed.yaml"


def env_without_credentials() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("MAILAUTH_")}
