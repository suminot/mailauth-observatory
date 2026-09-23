"""アクセス制御の検査。

このモジュールが存在する理由は1つで、**`access_control_configured: true`
が証拠にならない**ことである。人が YAML に書き込む真偽値なので、Access
アプリを作る前に true にしても、ポリシーの対象パスを間違えても true の
ままになる。個社明細の公開は取り返しがつかない。

したがってここでの最重要のテストは「確かめられなかったときに
通してしまわないこと」である。
"""

from __future__ import annotations

import httpx
import pytest

from mailauth import access


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def _cfg(**kw) -> access.AccessConfig:
    base = {
        "verify_base_url": "https://example.pages.dev",
        "protected_paths": ["/companies"],
        "idp": "entra_id",
        "allowed_email_domains": ["mkilabo.com"],
    }
    base.update(kw)
    return access.AccessConfig(**base)


# --------------------------------------------------------------------------
# 弾かれたと判断してよい場合
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "location",
    [
        "https://example.cloudflareaccess.com/cdn-cgi/access/login/example.pages.dev",
        "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?...",
        "/cdn-cgi/access/login/example.pages.dev",
    ],
)
def test_ログインへの転送は保護とみなす(location):
    c = _client(lambda req: httpx.Response(302, headers={"location": location}))
    r = access.probe("https://example.pages.dev/companies", client=c)
    assert r.state == access.PROTECTED


@pytest.mark.parametrize("status", [401, 403])
def test_拒否ステータスは保護とみなす(status):
    c = _client(lambda req: httpx.Response(status))
    r = access.probe("https://example.pages.dev/companies", client=c)
    assert r.state == access.PROTECTED


# --------------------------------------------------------------------------
# 通してはいけない場合
# --------------------------------------------------------------------------


def test_200_が返ったら公開されている():
    c = _client(lambda req: httpx.Response(200, text="<html>会社一覧</html>"))
    r = access.probe("https://example.pages.dev/companies", client=c)
    assert r.state == access.OPEN


def test_到達できなかったら保護とみなさない():
    """**ここが一番大事。** 確かめられなかったことを「守られている」と
    読むと、このモジュールを作った意味が消える。"""

    def boom(req):
        raise httpx.ConnectError("no route to host")

    r = access.probe("https://example.pages.dev/companies", client=_client(boom))
    assert r.state == access.UNKNOWN
    assert not r.protected


def test_認証と無関係な転送は判断しない():
    """末尾スラッシュの正規化などを認証の証拠にしない。"""
    c = _client(lambda req: httpx.Response(301, headers={"location": "/companies/"}))
    r = access.probe("https://example.pages.dev/companies", client=c)
    assert r.state == access.UNKNOWN


def test_404_は保護ではない():
    """無いものは守られているとは言えない。"""
    c = _client(lambda req: httpx.Response(404))
    r = access.probe("https://example.pages.dev/companies", client=c)
    assert r.state == access.UNKNOWN


def test_リダイレクトを追わない():
    """追うと、ログイン画面の 200 を「公開されている」と読み違える。"""
    seen = []

    def handler(req):
        seen.append(str(req.url))
        if "companies" in str(req.url):
            return httpx.Response(302, headers={"location": "https://login.microsoftonline.com/x"})
        return httpx.Response(200, text="Sign in")

    access.probe("https://example.pages.dev/companies", client=_client(handler))
    assert len(seen) == 1


# --------------------------------------------------------------------------
# 全体の判定
# --------------------------------------------------------------------------


def test_全パスが弾かれたときだけ検証済み():
    c = _client(lambda req: httpx.Response(403))
    report = access.verify(_cfg(protected_paths=["/companies", "/internal"]), client=c)
    assert report.verified


def test_1つでも開いていたら検証済みにしない():
    def handler(req):
        if str(req.url).endswith("/internal"):
            return httpx.Response(200, text="ok")
        return httpx.Response(403)

    report = access.verify(
        _cfg(protected_paths=["/companies", "/internal"]), client=_client(handler)
    )
    assert not report.verified
    assert any("認証なしで開いている" in r for r in report.reasons())


def test_1つでも確かめられなければ検証済みにしない():
    def handler(req):
        if str(req.url).endswith("/internal"):
            raise httpx.ConnectError("boom")
        return httpx.Response(403)

    report = access.verify(
        _cfg(protected_paths=["/companies", "/internal"]), client=_client(handler)
    )
    assert not report.verified


def test_基点urlが無ければ検証できない():
    report = access.verify(_cfg(verify_base_url=None))
    assert not report.verified
    assert "verify_base_url" in (report.unavailable_reason or "")


def test_対象パスが空なら検証できない():
    report = access.verify(_cfg(protected_paths=[]))
    assert not report.verified


# --------------------------------------------------------------------------
# 設定の読み取り
# --------------------------------------------------------------------------


def test_第2層のパスは設定漏れでも必ず検査する():
    """守るべき唯一の場所を検査しないまま「検査した」と言わせない。"""
    cfg = access.AccessConfig.from_publish_config(
        {"access": {"protected_paths": []}, "deploy": {"tier2_path": "/companies"}}
    )
    assert "/companies" in cfg.protected_paths


def test_第2層のパスを重複させない():
    cfg = access.AccessConfig.from_publish_config(
        {
            "access": {"protected_paths": ["/companies"]},
            "deploy": {"tier2_path": "/companies"},
        }
    )
    assert cfg.protected_paths.count("/companies") == 1


def test_実際の設定ファイルを読める():
    cfg = access.load()
    assert cfg.provider == "cloudflare_access"
    assert cfg.idp == "entra_id"
    assert "mkilabo.com" in cfg.allowed_email_domains
    # 第2層のパスは publish.yaml の deploy.tier2_path から自動で入る
    assert "/companies" in cfg.protected_paths


# --------------------------------------------------------------------------
# 第1層が閉じたままになるのを見つける
# --------------------------------------------------------------------------


def test_サイト全体を閉じていたら気付ける():
    report = access.AccessReport(config=_cfg(protected_paths=["/", "/companies"]))
    assert access.tier1_is_gated(report, "/companies")


def test_第2層だけなら警告しない():
    report = access.AccessReport(config=_cfg(protected_paths=["/companies"]))
    assert not access.tier1_is_gated(report, "/companies")
