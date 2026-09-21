"""mailauth doctor の診断。

このモジュールの要件は**「鍵が1つも無くても、今日できることが1つ出る」**ことで
ある。運営者の作業が9件あって着手できないという状態を作らないための機能なので、
「何も出ない」を許すとモジュールの存在意義が消える。
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from mailauth import doctor
from mailauth.cli import app

CRED_NAMES = list(doctor.CREDENTIALS)


@pytest.fixture(autouse=True)
def _no_credentials(monkeypatch, tmp_path):
    """既定は鍵ゼロ。.env を読ませないために MAILAUTH_ROOT も逃がす。"""
    for name in CRED_NAMES:
        monkeypatch.delenv(name, raising=False)
    for envs in doctor.DESTINATION_ENV.values():
        for e in envs:
            monkeypatch.delenv(e, raising=False)
    for e in ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_PAGES_PROJECT"):
        monkeypatch.delenv(e, raising=False)


@pytest.fixture
def empty_gold(monkeypatch, tmp_path):
    """gold をまだ作っていない状態にする。"""
    monkeypatch.setenv("MAILAUTH_GOLD_ROOT", str(tmp_path / "gold"))
    monkeypatch.setenv("MAILAUTH_DATA_ROOT", str(tmp_path / "data"))


def test_鍵が1つも無くても次の一手が出る(empty_gold):
    report = doctor.diagnose()
    assert report.next_action.headline
    assert report.next_action.steps, "手順が空だと「何をすればいいか」が伝わらない"


def test_鍵が無いときは一番早く手に入るものを指す(empty_gold):
    """申請制の gBizINFO ではなく、登録不要の連絡先を先に出すこと。

    待ち時間のあるものを先に出すと、待っている間に何も進まない。
    """
    report = doctor.diagnose()
    assert "MAILAUTH_CONTACT_EMAIL" in report.next_action.headline


def test_連絡先だけで米国母集団が回せる(monkeypatch, empty_gold):
    """SEC EDGAR はパブリックドメイン、Wikidata は CC0。

    登録も申請も要らない経路が1本あることが、着手のしやすさを支えている。
    ここが崩れると「何も始められない」状態が生まれる。
    """
    monkeypatch.setenv("MAILAUTH_CONTACT_EMAIL", "someone@example.com")
    report = doctor.diagnose()
    runnable = {p.id for p in report.runnable}
    assert "us-all-listed" in runnable
    assert "us-all-listed" in report.next_action.headline
    assert any("limit" in s for s in report.next_action.steps)


def test_国内母集団は鍵が揃うまで動かせない(empty_gold):
    report = doctor.diagnose()
    jp = next(p for p in report.populations if p.id == "jp-all-listed")
    assert not jp.runnable
    assert "MAILAUTH_EDINET_SUBSCRIPTION_KEY" in jp.missing_required


def test_法人番号は任意扱い(monkeypatch, empty_gold):
    """商号の裏取りが無くても計測は通る。必須に混ぜると着手の壁が1つ増える。"""
    monkeypatch.setenv("MAILAUTH_EDINET_SUBSCRIPTION_KEY", "x")
    monkeypatch.setenv("MAILAUTH_GBIZINFO_TOKEN", "y")
    report = doctor.diagnose()
    jp = next(p for p in report.populations if p.id == "jp-all-listed")
    assert jp.runnable
    assert "MAILAUTH_HOUJIN_BANGOU_APP_ID" in jp.missing_optional


def test_米国母集団は官報url欠損を許容しているので鍵を必須にしない(empty_gold):
    """us-all-listed の official_url は Wikidata（鍵不要）から来る。

    閾値が 0.80 に置かれているのは被覆率の低さが既知の制約だからで、
    ここを必須扱いにすると存在しない鍵を探すことになる。
    """
    report = doctor.diagnose()
    us = next(p for p in report.populations if p.id == "us-all-listed")
    assert us.missing_required == ["MAILAUTH_CONTACT_EMAIL"]


def test_あとでよいものに計測を止める要素が混ざらない(empty_gold):
    """退避先・公開先・辞書・第2層はいずれも計測の前提ではない。"""
    report = doctor.diagnose()
    keys = {i.key for i in report.later}
    assert keys == {"offload", "deploy", "dkim_l3", "tier2"}
    assert all(not i.done for i in report.later)


def test_計測済みなら次の一手が公開に移る(monkeypatch, tmp_path):
    monkeypatch.setenv("MAILAUTH_CONTACT_EMAIL", "someone@example.com")
    gold = tmp_path / "gold" / "month=2026-08"
    gold.mkdir(parents=True)
    (gold / "summary.parquet").write_bytes(b"x")
    monkeypatch.setenv("MAILAUTH_GOLD_ROOT", str(tmp_path / "gold"))
    monkeypatch.setenv("MAILAUTH_DATA_ROOT", str(tmp_path / "data"))
    report = doctor.diagnose()
    assert report.months == ["2026-08"]
    assert "Cloudflare" in report.next_action.headline


def test_診断は何も書き換えない(empty_gold, tmp_path):
    before = sorted(p.name for p in tmp_path.iterdir())
    doctor.diagnose()
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_cli_が動く(empty_gold):
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "次の一手" in result.stdout


def test_cli_json(empty_gold):
    result = CliRunner().invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["next_action"]["headline"]
    assert "populations" in payload


def test_鍵の一覧に取得元と影響が全部ある():
    """「どこで取るか」「無いと何が止まるか」が欠けた鍵を作らない。"""
    for cred in doctor.CREDENTIALS.values():
        assert cred.where
        assert cred.stops
        assert cred.cost in doctor.COST_LABELS
