"""公式サイトの TLS 証明書の SAN から兄弟ドメインを拾う（tls_san.py）。

**ネットワークには出ない。** 証明書の取得は `fetch` を差し替えて模す。
"""

from __future__ import annotations

import ssl

import pytest
from test_p2_p3 import CT, RUN, _run_p2, candidates, seeded_run  # noqa: F401

from mailauth.resolver import StaticResolver
from mailauth.tls_san import (
    SHARED_CERT_LIMIT,
    TlsSanSource,
    interpret,
    registrable_names,
)


def test_SAN_を登録ドメインにまとめる():
    names = ["www.example.co.jp", "*.example.co.jp", "example.co.jp", "shop.example-brand.jp"]
    assert registrable_names(names) == ["example-brand.jp", "example.co.jp"]


def test_共用の証明書からは何も拾わない():
    """CDN の共用 SAN 証明書には、無関係な会社のドメインが数十並ぶ。"""
    names = [f"customer{i}.com" for i in range(SHARED_CERT_LIMIT + 1)] + ["example.co.jp"]
    result = interpret("example.co.jp", "example.co.jp", names)
    assert result.shared
    assert result.usable == []


def test_起点で取れなければ_www_で取る():
    asked: list[str] = []

    def fetch(host):
        asked.append(host)
        if host == "example.co.jp":
            raise ConnectionRefusedError("no listener")
        return ["www.example.co.jp", "example-group.jp"]

    result = TlsSanSource(fetch).lookup("example.co.jp")
    assert asked == ["example.co.jp", "www.example.co.jp"]
    assert result.host == "www.example.co.jp"
    assert result.usable == ["example-group.jp", "example.co.jp"]


def test_検証に通らない証明書は使わない():
    """共用レンタルサーバーの証明書は、その会社の名前で検証できない。"""

    def fetch(host):
        raise ssl.SSLCertVerificationError("hostname mismatch")

    result = TlsSanSource(fetch).lookup("example.co.jp")
    assert result.error_kind == "verify"
    assert result.usable == []


def test_時間切れは別に数える():
    def fetch(host):
        raise TimeoutError()

    assert TlsSanSource(fetch).lookup("example.co.jp").error_kind == "timeout"


def test_まとめて取ると入力順に返す():
    source = TlsSanSource(lambda host: [host], concurrency=4)
    got = source.prefetch(["b.example", "a.example", "b.example", "c.example"])
    assert list(got) == ["b.example", "a.example", "c.example"]


def test_検査は本物の証明書を読みに行かない():
    """conftest が fetch_names を塞いでいる。差し替え忘れは即座に落ちる。"""
    with pytest.raises(AssertionError, match="証明書を読みに外へ出ようとした"):
        from mailauth import tls_san

        tls_san.fetch_names("example.co.jp")


@pytest.mark.usefixtures("seeded_run")
def test_P2_は_SAN_の兄弟ドメインを候補にする():
    sans = {
        "sample-info.co.jp": ["www.sample-info.co.jp", "sample-info-brand.jp"],
    }

    def fetch(host):
        bare = host.removeprefix("www.")
        if bare in sans:
            return sans[bare]
        raise ConnectionRefusedError(host)

    result = _run_p2(tls_source=TlsSanSource(fetch))
    df = candidates()
    hit = df[df["domain"] == "sample-info-brand.jp"]
    assert not hit.empty, "SAN に載っていた兄弟ドメインが候補になっていない"
    assert "tls_san" in set(hit["discovery_method"])
    stats = result["breakdown"]["tls_san"]
    assert stats["verified"] >= 1
    assert stats["entities_with_siblings"] == 1
    assert stats["errors_by_kind"].get("connect", 0) >= 1


@pytest.mark.usefixtures("seeded_run")
def test_offline_では証明書を読まず_そう書く():
    """注入しなければ、offline（検査の既定）では引かない。"""
    from mailauth.p2_candidates import run as run_p2

    result = run_p2(run_id=RUN, resolver=StaticResolver({}), ct_source=CT)
    codes = {w["code"] for w in result["warnings"]}
    assert "TLS_SAN_SKIPPED_OFFLINE" in codes
    assert result["breakdown"]["tls_san"]["looked_up"] == 0


def test_SAN_は独立した裏付けに数える():
    import yaml

    from mailauth.paths import repo_root

    cfg = yaml.safe_load(
        (repo_root() / "configs" / "candidates.yaml").read_text(encoding="utf-8")
    )
    assert cfg["discovery"]["tls_san"] is True
    assert "tls_san" in cfg["confidence"]["independent_methods"]


def test_CT_の共用証明書の行は捨てる():
    """crt.sh の1行は証明書1枚。共用 SAN 証明書の他社ドメインを候補にしない。"""
    from mailauth.ctlog import _extract

    shared = "\n".join(
        ["example.co.jp"] + [f"unrelated{i}.com" for i in range(SHARED_CERT_LIMIT + 2)]
    )
    rows = [
        {"name_value": "www.example.co.jp\nexample-brand.jp"},
        {"name_value": shared},
    ]
    got = _extract("example.co.jp", rows)
    assert got.found == ["example-brand.jp", "example.co.jp"]
    assert got.shared_skipped == 1
