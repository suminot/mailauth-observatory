"""MTA-STS のポリシーを読む（RFC 8461）。**ネットワークには出ない。**"""

from __future__ import annotations

import pytest

from mailauth.p5_parse.mta_sts_policy import (
    FetchStatus,
    PolicyFetch,
    evaluate,
    mx_matches,
    parse_policy,
)

ENFORCE = (
    "version: STSv1\r\n"
    "mode: enforce\r\n"
    "mx: mail.example.jp\r\n"
    "mx: *.mx.example.net\r\n"
    "max_age: 604800\r\n"
)


def test_正しいポリシーを読む():
    policy = parse_policy(ENFORCE)
    assert policy.valid
    assert policy.mode == "enforce"
    assert policy.mx == ["mail.example.jp", "*.mx.example.net"]
    assert policy.max_age == 604800


def test_LF_だけの改行でも読む():
    assert parse_policy(ENFORCE.replace("\r\n", "\n")).valid


@pytest.mark.parametrize(
    "text, fragment",
    [
        (ENFORCE.replace("STSv1", "STSv2"), "version"),
        (ENFORCE.replace("enforce", "strict"), "mode"),
        (ENFORCE.replace("604800", "forever"), "max_age"),
        (ENFORCE.replace("604800", "99999999"), "上限"),
        ("version: STSv1\nmode: enforce\nmax_age: 86400\n", "mx"),
        ("<html>404 Not Found</html>", "version"),
    ],
)
def test_壊れたポリシーは読めないと言う(text, fragment):
    policy = parse_policy(text)
    assert not policy.valid
    assert any(fragment in e for e in policy.errors)


def test_mode_none_は_mx_が無くてよい():
    assert parse_policy("version: STSv1\nmode: none\nmax_age: 86400\n").valid


@pytest.mark.parametrize(
    "host, ok",
    [
        ("mail.example.jp", True),
        ("MAIL.example.jp.", True),
        ("a.mx.example.net", True),
        # ワイルドカードは左端の1ラベルだけ
        ("a.b.mx.example.net", False),
        ("mx.example.net", False),
        ("other.example.jp", False),
    ],
)
def test_mx_の当て方は_RFC_8461_4_1(host, ok):
    assert mx_matches(host, ["mail.example.jp", "*.mx.example.net"]) is ok


def test_取れなかったポリシーに_mode_を付けない():
    """**取れないものは、送る側も読めない。** mode を推測で埋めない。"""
    out = evaluate(PolicyFetch(FetchStatus.REDIRECT, http_status=301), ["mail.example.jp"])
    assert out["mta_sts_policy_status"] == "redirect"
    assert out["mta_sts_mode"] is None
    assert out["mta_sts_mx_mismatch"] is None


def test_読めない中身は_invalid_policy():
    out = evaluate(PolicyFetch(FetchStatus.OK, body="hello"), ["mail.example.jp"])
    assert out["mta_sts_policy_status"] == "invalid_policy"
    assert out["mta_sts_mode"] is None
    assert out["mta_sts_policy_raw"] == "hello"


def test_MX_を移してポリシーを直し忘れると食い違いになる():
    out = evaluate(PolicyFetch(FetchStatus.OK, body=ENFORCE), ["x.mail.protection.outlook.com"])
    assert out["mta_sts_mode"] == "enforce"
    assert out["mta_sts_mx_mismatch"] is True


def test_MX_がポリシーに入っていれば食い違いではない():
    out = evaluate(
        PolicyFetch(FetchStatus.OK, body=ENFORCE), ["mail.example.jp", "z.mx.example.net"]
    )
    assert out["mta_sts_mx_mismatch"] is False


def test_mode_none_は食い違いを問わない():
    body = "version: STSv1\nmode: none\nmax_age: 86400\n"
    out = evaluate(PolicyFetch(FetchStatus.OK, body=body), ["anything.example"])
    assert out["mta_sts_mode"] == "none"
    assert out["mta_sts_mx_mismatch"] is None


def _mock_client(monkeypatch, handler):
    import httpx

    real = httpx.Client

    def fake_client(**kw):
        # RFC 8461 §3.3。追うと、送る側が失敗するポリシーを取れたと数える
        assert kw.get("follow_redirects") is False
        return real(transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(httpx, "Client", fake_client)


def test_live_fetcher_はリダイレクトを追わない(monkeypatch):
    import httpx

    from mailauth.p5_parse.mta_sts_policy import live_fetcher

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "mta-sts.example.jp"
        assert request.url.path == "/.well-known/mta-sts.txt"
        return httpx.Response(301, headers={"location": "https://elsewhere.example/"})

    _mock_client(monkeypatch, handler)
    got = live_fetcher("test-agent")("example.jp")
    assert got.status == "redirect"
    assert got.http_status == 301


def test_live_fetcher_は_200_の本文を返す(monkeypatch):
    import httpx

    from mailauth.p5_parse.mta_sts_policy import live_fetcher

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=ENFORCE, headers={"content-type": "text/plain"})

    _mock_client(monkeypatch, handler)
    got = live_fetcher("test-agent")("example.jp")
    assert got.status == "ok"
    assert parse_policy(got.body).mode == "enforce"


def test_live_fetcher_は_404_を_http_error_にする(monkeypatch):
    import httpx

    from mailauth.p5_parse.mta_sts_policy import live_fetcher

    _mock_client(monkeypatch, lambda request: httpx.Response(404))
    assert live_fetcher("test-agent")("example.jp").status == "http_error"


def test_P5_は_TXT_があるドメインだけポリシーを取る():
    """send.example.jp だけが `_mta-sts` を持つ（test_p5_parse の ANSWERS）。"""
    from test_p5_parse import ANSWERS, RUN, FakeBackend, _write_domains, facts

    from mailauth.p4_measure import run as run_p4
    from mailauth.p5_parse import run as run_p5
    from mailauth.resolver import StaticResolver

    asked: list[str] = []

    def fetcher(domain: str) -> PolicyFetch:
        asked.append(domain)
        return PolicyFetch(FetchStatus.OK, body=ENFORCE)

    _write_domains()
    run_p4(run_id=RUN, backend=FakeBackend())
    result = run_p5(run_id=RUN, resolver=StaticResolver(ANSWERS), policy_fetcher=fetcher)
    assert asked == ["send.example.jp"]
    send = facts().loc["d:1"]
    assert send["mta_sts_mode"] == "enforce"
    assert send["mta_sts_policy_status"] == "ok"
    # MX は x.mail.protection.outlook.com で、ポリシーの mx に無い
    assert bool(send["mta_sts_mx_mismatch"]) is True
    codes = {w["code"] for w in result["warnings"]}
    assert "MTA_STS_MX_MISMATCH" in codes
    assert "MTA_STS_POLICY_NOT_FETCHED" not in codes
    assert result["breakdown"]["mta_sts_mode_enforce"] == 1


def test_P5_はポリシーを取らなければそう書く():
    from test_p5_parse import ANSWERS, RUN, FakeBackend, _write_domains, facts

    from mailauth.p4_measure import run as run_p4
    from mailauth.p5_parse import run as run_p5
    from mailauth.resolver import StaticResolver

    _write_domains()
    run_p4(run_id=RUN, backend=FakeBackend())
    result = run_p5(run_id=RUN, resolver=StaticResolver(ANSWERS))
    assert "MTA_STS_POLICY_NOT_FETCHED" in {w["code"] for w in result["warnings"]}
    send = facts().loc["d:1"]
    import pandas as pd

    assert pd.isna(send["mta_sts_policy_status"])
