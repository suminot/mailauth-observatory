"""第2層（個社名付き明細）の手元向け明細。

**これは公開経路ではない。** `p8_publish` の第2層は事前通知から最低30日と
アクセス制御が揃うまで出せない。ここは運営者が手元で見るためのもので、
置き場所を間違えるとその条件を回避したことになる。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from mailauth import drilldown

# ===========================================================================
# 置き場所


@pytest.mark.parametrize("bad", ["site/src/tier2.html", "site/x.html", "gold/x.html"])
def test_公開経路には書かない(bad, monkeypatch, tmp_path):
    """**置いた瞬間にアクセス制御の内側ではなくなる。**

    「気を付ける」では足りない。第2層の公開条件に直結するので機械で拒む。
    """
    with pytest.raises(drilldown.PublicPathRefused):
        drilldown._refuse_public_path(Path(bad))


@pytest.mark.parametrize("ok", ["data/runs/2026-09/tier2.html", "/tmp/x/tier2.html"])
def test_手元と成果物には書ける(ok):
    """拒みすぎると使えない。**run ディレクトリと手元は通す。**"""
    drilldown._refuse_public_path(Path(ok))


# ===========================================================================
# 中身


def _fixture(tmp_path, monkeypatch, *, observed=True, dmarc=True,
             reason="DNS 上に痕跡を残さない製品がある"):
    """最小の run ディレクトリを作る。"""
    monkeypatch.setenv("MAILAUTH_DATA_ROOT", str(tmp_path / "data"))
    run = "2026-09"
    base = tmp_path / "data" / "runs" / run

    def put(phase, name, df):
        d = base / phase
        d.mkdir(parents=True, exist_ok=True)
        df.to_parquet(d / name)

    put("p1_population", "entities.parquet", pd.DataFrame([{
        "entity_id": "jp:1", "name": "試験株式会社", "securities_code": "0000",
        "industry_label": "素材・化学",
    }]))
    put("p3_domains", "domains.parquet", pd.DataFrame([{
        "domain_id": "d1", "entity_id": "jp:1", "domain": "example.co.jp",
        "domain_role": "sending", "confidence": "confirmed", "dkim_found": True,
    }]))
    put("p5_parse", "facts.parquet", pd.DataFrame([{
        "domain_id": "d1", "entity_id": "jp:1", "observed": observed,
        "spf_present": True, "spf_all_qualifier": "-all", "spf_exceeds_limit": False,
        "dmarc_present": dmarc, "dmarc_p": "reject" if dmarc else None,
        "dmarc_pct": 100 if dmarc else None, "policy_label": "enforced" if dmarc else None,
        "blind_enforcement": False, "dkim_status": "found",
        "dkim_wildcard_suspect": False, "mta_sts_mode": "enforce",
        "tls_rpt_present": True, "bimi_present": False, "dnssec_signed": False,
        "mx_hosts": ["mx.example.co.jp"],
    }]))
    put("p6_infer", "inferences.parquet", pd.DataFrame([{
        "domain_id": "d1", "category": "mail_platform", "vendor": "Microsoft",
        "undetectable_reason": reason,
    }]))
    return run


def test_企業名が入る(tmp_path, monkeypatch):
    """**第2層の目的そのもの。** 名前が出なければ意味がない。"""
    run = _fixture(tmp_path, monkeypatch)
    rows = drilldown.build_rows(run)
    assert rows and rows[0]["企業名"] == "試験株式会社"
    assert rows[0]["ドメイン"] == "example.co.jp"


def test_観測できていないことを空欄にしない(tmp_path, monkeypatch):
    """**原則5。** 名指しの表で一番危ないのは、こちらが観測できなかった
    ことを「その企業が対策していない」と読ませること。
    """
    run = _fixture(tmp_path, monkeypatch, observed=None, dmarc=None)
    rows = drilldown.build_rows(run)
    assert rows[0]["観測"] == "不明", rows[0]["観測"]
    assert rows[0]["DMARC"] == "不明", rows[0]["DMARC"]
    # **「無し」と混ざらないこと。** 混ざると取り返しがつかない
    assert rows[0]["DMARC"] != "無し"


def test_検出できない理由を持ち歩く(tmp_path, monkeypatch):
    """**「DNS から分からない」を空欄にすると「使っていない」に見える。**"""
    run = _fixture(tmp_path, monkeypatch)
    rows = drilldown.build_rows(run)
    assert "痕跡" in rows[0]["検出できない理由"]


def test_社外秘であることが画面に出る(tmp_path, monkeypatch):
    """**持ち出しの判断をする人が、開いた瞬間に分かること。**"""
    run = _fixture(tmp_path, monkeypatch)
    out = tmp_path / "out" / "t.html"
    path, n = drilldown.write_report(run, out)
    text = path.read_text(encoding="utf-8")
    assert n == 1
    assert "社外秘" in text
    assert "公開してはならない" in text
    # 限界の断りが表より前に出ていること
    assert text.index("対策していないのではなく") < text.index("<table")


def test_外部への読み込みを持たない(tmp_path, monkeypatch):
    """**手元で開くものが外へ通信を出さない。**

    開いた人の環境から、見ていることが外に漏れる経路を作らない。
    """
    run = _fixture(tmp_path, monkeypatch)
    path, _ = drilldown.write_report(run, tmp_path / "t.html")
    text = path.read_text(encoding="utf-8")
    for bad in ("http://", "https://", "//cdn", "src=\"//"):
        assert bad not in text, f"外部への読み込みがある: {bad}"


# ===========================================================================
# ワークフロー


def _monthly() -> str:
    from tests.test_compliance import repo_root

    return (repo_root() / ".github" / "workflows" / "monthly.yml").read_text(encoding="utf-8")


def test_個社別明細が公開サイトに載らない():
    """**成果物にはするが、公開サイトには出さない。**

    成果物は GitHub の認証の内側にあり、リポジトリの権限を持つ人しか
    取れない。公開サイトのデプロイ（`site/dist`）には入らない。
    """
    text = _monthly()
    assert "mailauth drilldown" in text, "個社別明細を作っていない"
    block = text[text.index("個社別明細を作る") :]
    block = block[: block.index("公開しない実行でも")]
    assert "upload-artifact" in block, "成果物にしていない"
    assert "site/" not in block, "公開サイトの経路に書こうとしている"
    assert "gold/" not in block, "公開データセットの経路に書こうとしている"


def test_明細が社外秘だと名前で分かる():
    """**成果物の一覧で取り違えない。** 名前と表示に断りを入れる。"""
    text = _monthly()
    block = text[text.index("個社別明細を作る") :]
    block = block[: block.index("公開しない実行でも")]
    assert "社外秘" in block
    assert "tier2" in block, "第2層だと分かる名前になっていない"


def test_検出できない理由を日本語で出す(tmp_path, monkeypatch):
    """**識別子のままだと「何かのエラー」に見える。**

    いちばん大事な「使っていないのではない」が伝わらない。
    `api_mode_product` のような値は日本語に直して出す。
    """
    from mailauth.contracts import UndetectableReason

    run = _fixture(
        tmp_path, monkeypatch, reason=UndetectableReason.SELF_HOSTED_MX.value
    )
    rows = drilldown.build_rows(run)
    got = rows[0]["検出できない理由"]
    assert "自社運用" in got, got
    assert "self_hosted" not in got


def test_知らない理由は捨てずにそのまま出す(tmp_path, monkeypatch):
    """**表から消すのが一番まずい。** 訳せないなら原文で出す。"""
    run = _fixture(tmp_path, monkeypatch, reason="まだ名前の無い理由")
    rows = drilldown.build_rows(run)
    assert rows[0]["検出できない理由"] == "まだ名前の無い理由"
