"""テスト共通のフィクスチャ。

テストは一切ネットワークに出ない。EDINET / gBizINFO / 国税庁のいずれも、
ローカルのフィクスチャか未設定スキップで賄えるようにしてある。

**それを書いておくだけでは守られなかった。**

外部 API を叩く補完は、これまで偶然守られていただけだった ── gBizINFO も
国税庁も認証情報が無ければ勝手に止まるので、鍵を置かない CI では
ネットワークに出なかった。そこに**鍵の要らない Wikidata** を足した途端、
P1 を回すすべての検査が本当に WDQS を叩き始め、**CI が19分止まった。**
手元では出口 IP が弾かれて即座に 403 になるため、気付けなかった。

規約は言葉ではなく仕組みで守る。下の `no_real_network` が実際の送信層を
塞いでいる。`httpx.MockTransport` を使う検査は通る ── 塞ぐのは
「本当に外へ出る経路」だけである。
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

FIXTURES = Path(__file__).parent / "fixtures"
EDINET_SAMPLE = FIXTURES / "EdinetcodeDlInfo_sample.csv"


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """**本当に外へ出ようとしたら、待たずに落とす。**

    塞ぐのは実際の送信層（`httpx.HTTPTransport`）だけ。`MockTransport` を
    差し込んで応答を模している検査はそのまま通る。

    落とすのは「遅いから」ではない。**外の状態でテストの結果が変わる**のが
    問題で、しかも手元と CI で違う壊れ方をする（手元は即 403、CI は無言で
    数十分待つ）。
    """

    def refuse(self, request, *args, **kwargs):
        raise AssertionError(
            f"テストがネットワークに出ようとした: {request.method} {request.url}\n"
            "応答を模すか、offline で回すこと（tests/conftest.py 参照）"
        )

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)


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


@pytest.fixture
def access_verified(monkeypatch):
    """アクセス制御の検査を「確認済み」に固定する。

    第2層のゲートは複数ある（訂正期間・未処理の訂正申告・アクセス制御）。
    **そのうち1つを試すテストで、ネットワークに出る検査まで走らせない。**
    アクセス制御の検査そのものは tests/test_access.py が見ている。
    """
    from mailauth import access

    def _verified(config, **kw):
        report = access.AccessReport(config=config)
        report.probes = [
            access.ProbeResult(url="https://example.test/companies", state=access.PROTECTED)
        ]
        return report

    monkeypatch.setattr(access, "verify", _verified)
