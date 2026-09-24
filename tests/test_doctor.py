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


def test_国内母集団は_gbizinfo_が無いと動かせない(empty_gold):
    report = doctor.diagnose()
    jp = next(p for p in report.populations if p.id == "jp-all-listed")
    assert not jp.runnable
    assert jp.missing_required == ["MAILAUTH_GBIZINFO_TOKEN"]


def test_edinet_の鍵は必須にしない(empty_gold):
    """EDINET コードリストは認証の要らない静的な zip である。

    2026-09 の実行が鍵なしで 11,386 件を取得している。**必須でないものを
    必須として出すと、着手の障壁を実際より高く見せる**ので、任意側に置く。
    """
    report = doctor.diagnose()
    jp = next(p for p in report.populations if p.id == "jp-all-listed")
    assert "MAILAUTH_EDINET_SUBSCRIPTION_KEY" not in jp.missing_required
    assert "MAILAUTH_EDINET_SUBSCRIPTION_KEY" in jp.missing_optional


def test_法人番号は任意扱い(monkeypatch, empty_gold):
    """商号の裏取りが無くても計測は通る。必須に混ぜると着手の壁が1つ増える。"""
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


def _write_gold(tmp_path, month: str, observed: int, population: str = "us-all-listed"):
    import pandas as pd

    d = tmp_path / "gold" / f"month={month}"
    d.mkdir(parents=True)
    pd.DataFrame(
        [{"population_id": population, "observed_domains": observed, "total_entities": 100}]
    ).to_parquet(d / "stats_overall.parquet")
    return d


def test_計測済みなら次の一手が公開に移る(monkeypatch, tmp_path):
    monkeypatch.setenv("MAILAUTH_CONTACT_EMAIL", "someone@example.com")
    _write_gold(tmp_path, "2026-08", observed=1200)
    monkeypatch.setenv("MAILAUTH_GOLD_ROOT", str(tmp_path / "gold"))
    monkeypatch.setenv("MAILAUTH_DATA_ROOT", str(tmp_path / "data"))
    report = doctor.diagnose()
    assert report.months == ["2026-08"]
    assert "Cloudflare" in report.next_action.headline


def test_回っただけで観測0の月を計測済みと数えない(monkeypatch, tmp_path):
    """**ファイルがあることと測れたことは別である**（原則5）。

    2026-09 の実行は gold を書き、実行レポートも success と言ったが、
    official_url が1件も取れず observed_domains は 0 だった。ディレクトリの
    有無で判定すると、空の結果を根拠に「次は公開」と勧めることになる。
    """
    _write_gold(tmp_path, "2026-09", observed=0, population="jp-all-listed")
    monkeypatch.setenv("MAILAUTH_GOLD_ROOT", str(tmp_path / "gold"))
    monkeypatch.setenv("MAILAUTH_DATA_ROOT", str(tmp_path / "data"))
    report = doctor.diagnose()
    assert report.months == []
    assert report.empty_months == ["2026-09"]
    assert "測れていない" in report.next_action.headline
    assert "Cloudflare" not in report.next_action.headline


def test_空振りの原因はその月に回した母集団のものを出す(monkeypatch, tmp_path):
    """全母集団から一番安い鍵を選ぶと、関係のない鍵を指すことになる。

    jp-all-listed で空振りしたなら、指すのは gBizINFO であって、
    別の母集団の MAILAUTH_CONTACT_EMAIL ではない。
    """
    _write_gold(tmp_path, "2026-09", observed=0, population="jp-all-listed")
    monkeypatch.setenv("MAILAUTH_GOLD_ROOT", str(tmp_path / "gold"))
    monkeypatch.setenv("MAILAUTH_DATA_ROOT", str(tmp_path / "data"))
    steps = doctor.diagnose().next_action.steps
    joined = "\n".join(steps)
    assert "MAILAUTH_GBIZINFO_TOKEN を入れると jp-all-listed が通る" in joined


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


# ===========================================================================
# 起点が取れていない企業がいることを、doctor が次の一手に出す
#
# **47.7% 欠けていても status=success で通っていた**（2026-09）。
# observed_domains が 0 のときだけ official_url を疑う作りだったため、
# 「半分測れている」は正常として素通りしていた。


def _all_set_up(monkeypatch):
    """公開も退避も済ませる。**設定の残件が無い状態で何を言うかを見る。**"""
    for name in ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_PAGES_PROJECT"):
        monkeypatch.setenv(name, "x")
    from mailauth.offload import DESTINATION_ENV

    for envs in DESTINATION_ENV.values():
        for e in envs:
            monkeypatch.setenv(e, "x")


def _write_gold_with_coverage(tmp_path, month, *, total, with_domains, observed=1200):
    import pandas as pd

    d = tmp_path / "gold" / f"month={month}"
    d.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "population_id": "jp-all-listed",
                "observed_domains": observed,
                "total_entities": total,
                "entities_with_domains": with_domains,
            }
        ]
    ).to_parquet(d / "stats_overall.parquet")
    return d


def test_起点が取れていない企業がいることを次の一手に出す(monkeypatch, tmp_path):
    _all_set_up(monkeypatch)
    _write_gold_with_coverage(tmp_path, "2026-09", total=3818, with_domains=1992)
    monkeypatch.setenv("MAILAUTH_GOLD_ROOT", str(tmp_path / "gold"))
    monkeypatch.setenv("MAILAUTH_DATA_ROOT", str(tmp_path / "data"))

    action = doctor.diagnose().next_action
    assert "1826 社が計測に現れていない" in action.headline
    assert "月次の確認だけでよい" not in action.headline


def test_ほとんど取れていれば黙っている(monkeypatch, tmp_path):
    """**欠けが小さい月まで毎回指摘すると、注意書きが背景になる。**"""
    _all_set_up(monkeypatch)
    _write_gold_with_coverage(tmp_path, "2026-09", total=3818, with_domains=3700)
    monkeypatch.setenv("MAILAUTH_GOLD_ROOT", str(tmp_path / "gold"))
    monkeypatch.setenv("MAILAUTH_DATA_ROOT", str(tmp_path / "data"))

    assert "月次の確認だけでよい" in doctor.diagnose().next_action.headline


def test_列が無い月を全社欠けていると読まない(monkeypatch, tmp_path):
    """この指標より前に回した gold には列が無い。

    **無い列を 0 と読むと「全社が計測に現れていない」ことになる**（原則5）。
    """
    _all_set_up(monkeypatch)
    _write_gold(tmp_path, "2026-08", observed=1200, population="jp-all-listed")
    monkeypatch.setenv("MAILAUTH_GOLD_ROOT", str(tmp_path / "gold"))
    monkeypatch.setenv("MAILAUTH_DATA_ROOT", str(tmp_path / "data"))

    assert "計測に現れていない" not in doctor.diagnose().next_action.headline


def test_設定が終わっていなければそちらが先(monkeypatch, tmp_path):
    """公開先すら無い段階で分母の話を出さない。**手順が前後する。**"""
    _write_gold_with_coverage(tmp_path, "2026-09", total=3818, with_domains=1992)
    monkeypatch.setenv("MAILAUTH_GOLD_ROOT", str(tmp_path / "gold"))
    monkeypatch.setenv("MAILAUTH_DATA_ROOT", str(tmp_path / "data"))

    assert "Cloudflare" in doctor.diagnose().next_action.headline


# ===========================================================================
# CT ログの取得先が落ちていた月
#
# 2026-09-24、crt.sh はトップページごと 502 を返していた。その最中に
# 回した月は**計測が成立していない** ── 候補が少ないのは実態ではない。
# 放っておくと、その月が翌月以降の比較の基準になる。


def _write_p2_manifest(tmp_path, run_id, *, codes):
    import json as _json

    d = tmp_path / "data" / "runs" / run_id / "p2_candidates"
    d.mkdir(parents=True)
    (d / "_manifest.json").write_text(
        _json.dumps({"warnings": [{"code": c, "message": c} for c in codes]}),
        encoding="utf-8",
    )


def test_相手が落ちていた月を次の一手に出す(monkeypatch, tmp_path):
    _all_set_up(monkeypatch)
    _write_gold_with_coverage(tmp_path, "2026-09", total=3818, with_domains=3700)
    _write_p2_manifest(tmp_path, "2026-09", codes=["CT_UPSTREAM_UNAVAILABLE"])
    monkeypatch.setenv("MAILAUTH_GOLD_ROOT", str(tmp_path / "gold"))
    monkeypatch.setenv("MAILAUTH_DATA_ROOT", str(tmp_path / "data"))

    action = doctor.diagnose().next_action
    assert "2026-09" in action.headline
    assert "落ちている" in action.headline
    # **同じ run_id で流し直せばキャッシュが効く**ことが書いてあること。
    # 書いていないと、読み手は最初から取り直すと思って先延ばしにする
    assert any("同じ run_id" in s for s in action.steps), action.steps


def test_分母の欠けより先に出す(monkeypatch, tmp_path):
    """**「そういう月だった」と「測れていない月」では、後者が先。**

    分母の欠けは数字として読めるが、相手が落ちていた月は読めない。
    しかも流し直すならキャッシュが温かいうちが一番安い。
    """
    _all_set_up(monkeypatch)
    _write_gold_with_coverage(tmp_path, "2026-09", total=3818, with_domains=1992)
    _write_p2_manifest(tmp_path, "2026-09", codes=["CT_UPSTREAM_UNAVAILABLE"])
    monkeypatch.setenv("MAILAUTH_GOLD_ROOT", str(tmp_path / "gold"))
    monkeypatch.setenv("MAILAUTH_DATA_ROOT", str(tmp_path / "data"))

    assert "計測に現れていない" not in doctor.diagnose().next_action.headline


def test_落ちていなければその話をしない(monkeypatch, tmp_path):
    """**当てはまらない説明を毎月添えると、次から読まれなくなる。**"""
    _all_set_up(monkeypatch)
    _write_gold_with_coverage(tmp_path, "2026-09", total=3818, with_domains=3700)
    _write_p2_manifest(tmp_path, "2026-09", codes=["ACCEPTANCE_MEDIAN_OUT_OF_RANGE"])
    monkeypatch.setenv("MAILAUTH_GOLD_ROOT", str(tmp_path / "gold"))
    monkeypatch.setenv("MAILAUTH_DATA_ROOT", str(tmp_path / "data"))

    assert "落ちている" not in doctor.diagnose().next_action.headline
