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
    # idp は未設定でよい（アクセス制御をまだ掛けていない）が、値を入れるなら
    # 想定している方式のどれかであること。綴り違いを検査に通さない
    assert cfg.idp is None or cfg.idp in access.KNOWN_IDPS
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


# --------------------------------------------------------------------------
# 検索エンジンに載せない
# --------------------------------------------------------------------------


def test_ヘッダがあれば載らないと判断する():
    c = httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, headers={"x-robots-tag": "noindex, nofollow"})
        ),
        follow_redirects=True,
    )
    r = access.probe_noindex("https://example.pages.dev/", client=c)
    assert r.state == access.PROTECTED


def test_metaだけならヘッダの不足を指摘する():
    """**meta robots は HTML にしか効かない。**

    公開データ（JSON / CSV / Parquet）を直接リンクされた場合に届かないので、
    「meta があるから大丈夫」で済ませない。
    """
    html = '<html><head><meta name="robots" content="noindex"></head></html>'
    c = httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200, text=html, headers={"content-type": "text/html; charset=utf-8"}
            )
        ),
        follow_redirects=True,
    )
    r = access.probe_noindex("https://example.pages.dev/", client=c)
    assert r.state == access.UNKNOWN
    assert r.meta is True
    assert "X-Robots-Tag" in r.detail


def test_指定が無ければ載りうると言う():
    c = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text="<html></html>")),
        follow_redirects=True,
    )
    r = access.probe_noindex("https://example.pages.dev/", client=c)
    assert r.state == access.OPEN


def test_到達できなければ載らないとは言わない():
    def boom(req):
        raise httpx.ConnectError("boom")

    c = httpx.Client(transport=httpx.MockTransport(boom), follow_redirects=True)
    r = access.probe_noindex("https://example.pages.dev/", client=c)
    assert r.state == access.UNKNOWN


# --------------------------------------------------------------------------
# 3層が揃っていること（設定漏れで静かに外れるのを防ぐ）
# --------------------------------------------------------------------------


def test_headers_に_x_robots_tag_がある():
    from mailauth.paths import repo_root

    text = (repo_root() / "site" / "static" / "_headers").read_text(encoding="utf-8")
    assert "X-Robots-Tag" in text
    assert "noindex" in text


def test_robots_txt_が巡回を止めていない():
    """`Disallow: /` と noindex を併用してはいけない。

    取りに来られなければ noindex を読めないので、他サイトからリンクされて
    いれば URL だけが検索結果に残りうる。**索引から外したいなら、
    巡回させて noindex を読ませる。**
    """
    from mailauth.paths import repo_root

    text = (repo_root() / "site" / "static" / "robots.txt").read_text(encoding="utf-8")
    directives = [
        line.strip() for line in text.splitlines() if line.strip().lower().startswith("disallow:")
    ]
    assert directives, "Disallow 行が無い"
    for line in directives:
        value = line.split(":", 1)[1].strip()
        assert value == "", f"巡回を止めている: {line}"


def test_ページに_meta_robots_がある():
    from mailauth.paths import repo_root

    text = (repo_root() / "site" / "observablehq.config.js").read_text(encoding="utf-8")
    assert 'name="robots"' in text
    assert "noindex" in text


def test_静的ファイルが配られる仕組みが残っている():
    """Observable Framework は**ページから参照されないファイルを配らない。**

    src/ に置いただけでは dist/ に現れず、検索避けの設定が配信されない。
    コピーの段取りが外れていないことを検査する。
    """
    import json

    from mailauth.paths import repo_root

    pkg = json.loads((repo_root() / "site" / "package.json").read_text(encoding="utf-8"))
    assert "copy-static" in pkg["scripts"]["build"]
    assert (repo_root() / "site" / "scripts" / "copy-static.mjs").is_file()


def test_otp_はメールドメインの条件が無いと通さない():
    """OTP は「そのアドレスに届くコードを入力できる」ことしか確かめない。

    **弾かれたことは外から確かめられても、誰が通れるのかは分からない。**
    設定だけで分かる不備なので、パスが弾かれていても検証済みにしない。
    """
    c = _client(lambda req: httpx.Response(403))
    report = access.verify(_cfg(idp=access.ONE_TIME_PIN, allowed_email_domains=[]), client=c)
    assert all(p.protected for p in report.probes)
    assert not report.verified
    assert any("誰でも通る" in r for r in report.reasons())


def test_otp_でもドメイン条件があれば通す():
    c = _client(lambda req: httpx.Response(403))
    report = access.verify(
        _cfg(idp=access.ONE_TIME_PIN, allowed_email_domains=["mkilabo.com"]), client=c
    )
    assert report.verified
