"""調査から足した P5・P7 の項目（BACKLOG 49）。

- SPF の先のドメインが登録されていない（SubdoMailing の形）
- 主ドメインの TXT の応答の大きさ
- DKIM の鍵が Ed25519 だけ
- DMARC に RFC 9989 で無くなったタグ

テストはネットワークに出ない。
"""

from __future__ import annotations

import datetime as dt

from mailauth.p5_parse.runner import estimate_txt_response_bytes, parse_domain
from mailauth.resolver import StaticResolver, make_answer

DOMAIN = "example-co.jp"


def _spf_rec(values: list[str], protocol: str | None = None) -> dict:
    rec = {
        "domain": DOMAIN, "purpose": "spf", "query_name": DOMAIN,
        "observed": True, "record_present": bool(values),
        "answers": [{"data": [v]} for v in values],
    }
    if protocol:
        rec["protocol"] = protocol
    return rec


def _resolver(entries: dict) -> StaticResolver:
    table = {}
    for (name, rtype), spec in entries.items():
        if isinstance(spec, dict):
            table[(name, rtype)] = make_answer(name, rtype, **spec)
        else:
            table[(name, rtype)] = make_answer(name, rtype, spec)
    return StaticResolver(table)


# -- SPF の先が登録されていない ---------------------------------------------------


def test_SPF_の先のドメインが空いていれば立てる():
    resolver = _resolver({
        ("expired-vendor.com", "NS"): {"rcode": "NXDOMAIN"},
        ("com", "NS"): ["a.gtld-servers.net."],
    })
    spf = _spf_rec(["v=spf1 include:_spf.expired-vendor.com -all"])
    fact = parse_domain(DOMAIN, {"spf": [spf]}, resolver=resolver)
    assert fact["spf_target_unregistered"] is True
    assert fact["spf_target_broken"] is True


def test_SPF_の先が登録されていれば立てない():
    resolver = _resolver({
        ("_spf.vendor.example", "TXT"): ["v=spf1 ip4:192.0.2.0/24 -all"],
    })
    fact = parse_domain(DOMAIN, {"spf": [_spf_rec(["v=spf1 include:_spf.vendor.example -all"])]},
                        resolver=resolver)
    assert fact["spf_target_unregistered"] is False


def test_SPF_の先に名前だけ無くても登録ドメインがあれば立てない():
    resolver = _resolver({("vendor.example", "NS"): ["ns1.vendor.example."]})
    fact = parse_domain(DOMAIN, {"spf": [_spf_rec(["v=spf1 include:_old.vendor.example -all"])]},
                        resolver=resolver)
    assert fact["spf_target_unregistered"] is False


def test_引けなかった先があれば分からないと言う():
    resolver = _resolver({
        ("_spf.down.example", "TXT"): {"observed": False, "rcode": "SERVFAIL"},
    })
    fact = parse_domain(DOMAIN, {"spf": [_spf_rec(["v=spf1 include:_spf.down.example -all"])]},
                        resolver=resolver)
    assert fact["spf_target_unregistered"] is None


def test_resolver_が無ければ_null():
    fact = parse_domain(DOMAIN, {"spf": [_spf_rec(["v=spf1 include:_spf.x.example -all"])]})
    assert fact["spf_target_unregistered"] is None


# -- TXT の大きさ ----------------------------------------------------------------


def test_TXT_の応答の大きさを見積もる():
    # ヘッダ 12 + 質問（"a.jp" → 6 + 4）+ OPT 11 + レコード 12 + (1 + 11)
    assert estimate_txt_response_bytes("a.jp", [["v=spf1 -all"]]) == 12 + 6 + 4 + 11 + 12 + 12
    # 255 で分けた文字列はそれぞれ長さの 1 バイトが付く
    assert estimate_txt_response_bytes("a.jp", [["x" * 255, "y"]]) == 33 + 12 + 256 + 2


def test_所有確認が積もった_TXT_は_1232_を超える():
    tokens = [f"google-site-verification={'A' * 43}{i}" for i in range(20)]
    fact = parse_domain(DOMAIN, {"spf": [_spf_rec(["v=spf1 -all", *tokens], protocol="tcp")]})
    assert fact["txt_apex_count"] == 21
    assert fact["txt_apex_bytes"] > 1232
    assert fact["txt_apex_tcp"] is True


def test_TXT_が無い_引けていなければ_null():
    fact = parse_domain(DOMAIN, {"spf": [{
        "domain": DOMAIN, "purpose": "spf", "query_name": DOMAIN, "observed": False,
    }]})
    assert (fact["txt_apex_count"], fact["txt_apex_bytes"], fact["txt_apex_tcp"]) == (
        None, None, None,
    )


# -- DKIM が Ed25519 だけ --------------------------------------------------------


def _dkim(selectors: dict[str, str], control: bool = False) -> dict:
    recs = [{
        "domain": DOMAIN, "purpose": "dkim", "query_name": f"{s}._domainkey.{DOMAIN}",
        "observed": True, "record_present": True, "answers": [{"data": [txt]}],
    } for s, txt in selectors.items()]
    out = {"dkim": recs}
    if control:
        out["dkim_control"] = [{
            "domain": DOMAIN, "purpose": "dkim_control", "observed": True,
            "record_present": True, "answers": [{"data": ["v=DKIM1; p=AAAA"]}],
        }]
    return out


ED = "v=DKIM1; k=ed25519; p=11qYAYKxCrfVS/7TyWQHOg7hcvPapiMlrwIaaPcHURo="
RSA = "v=DKIM1; k=rsa; p=" + "A" * 392


def test_Ed25519_の鍵しか無ければ立てる():
    assert parse_domain(DOMAIN, _dkim({"s1": ED}))["dkim_ed25519_only"] is True


def test_RSA_と並べていれば立てない():
    assert parse_domain(DOMAIN, _dkim({"s1": ED, "s2": RSA}))["dkim_ed25519_only"] is False


def test_鍵が無い_何にでも答える_DNS_は_null():
    assert parse_domain(DOMAIN, {})["dkim_ed25519_only"] is None
    assert parse_domain(DOMAIN, _dkim({"s1": ED}, control=True))["dkim_ed25519_only"] is None


# -- DMARC の廃止タグ ------------------------------------------------------------


def _dmarc(txt: str) -> dict:
    return {"dmarc": [{
        "domain": DOMAIN, "purpose": "dmarc", "query_name": f"_dmarc.{DOMAIN}",
        "observed": True, "record_present": True, "answers": [{"data": [txt]}],
    }]}


def test_DMARC_の廃止タグを見つける():
    def tags(by_purpose):
        return parse_domain(DOMAIN, by_purpose)["dmarc_historic_tags"]

    assert tags(_dmarc("v=DMARC1; p=reject; pct=100")) is True
    assert tags(_dmarc("v=DMARC1; p=none; ri=86400")) is True
    assert tags(_dmarc("v=DMARC1; p=reject; t=n")) is False
    assert tags({}) is None


# -- P7 --------------------------------------------------------------------------


def _row(i, **kw):
    from mailauth.p7_aggregate.metrics import DomainRow

    base = {"domain_id": f"d:{i}", "entity_id": f"jp:{i}", "is_apex": True, "observed": True,
            "spf_present": True, "dmarc_present": True}
    base.update(kw)
    return DomainRow.from_fact(base)


def test_集計は確かめられたものだけ数える():
    from mailauth.p7_aggregate.metrics import aggregate

    rows = [
        _row(1, spf_target_unregistered=True, txt_apex_bytes=1500, dkim_ed25519_only=True,
             dmarc_historic_tags=True),
        _row(2, spf_target_unregistered=False, txt_apex_bytes=400, dkim_ed25519_only=False,
             dmarc_historic_tags=False),
        _row(3, spf_target_unregistered=None, txt_apex_bytes=None, dkim_ed25519_only=None,
             dmarc_historic_tags=None),
        _row(4, spf_target_unregistered=float("nan"), txt_apex_bytes=float("nan")),
    ]
    s = aggregate(rows, measured_month=dt.date(2026, 12, 1), population_id="jp-all-listed",
                  total_entities=4)
    assert (s.spf_dangling_domains, s.txt_apex_oversize_domains,
            s.dkim_ed25519_only_domains, s.dmarc_historic_tags_domains) == (1, 1, 1, 1)


def test_列の無い過去の_run_は_0_ではなく_null():
    from mailauth.p7_aggregate.metrics import aggregate

    s = aggregate([_row(1)], measured_month=dt.date(2026, 12, 1), population_id="jp-all-listed",
                  total_entities=1)
    assert (s.spf_dangling_domains, s.txt_apex_oversize_domains,
            s.dkim_ed25519_only_domains, s.dmarc_historic_tags_domains) == (
        None, None, None, None,
    )


# -- SPF が通す IP アドレスの広さ -------------------------------------------------


def test_通すと書いた_IPv4_を重なりを除いて数える():
    from mailauth.p5_parse.spf import address_space
    from mailauth.records import spf_terms

    space = address_space(spf_terms(
        "v=spf1 ip4:192.0.2.0/24 ip4:192.0.2.128/25 +ip4:198.51.100.7 "
        "-ip4:10.0.0.0/8 ~ip4:172.16.0.0/12 ip4:bad/99 ip6:2001:db8::/32 include:x.example -all"
    ))
    assert space.ip4_addresses == 256 + 1
    assert space.ip4_widest_prefix == 24
    assert space.ip6_widest_prefix == 32


def test_fact_と集計に広すぎる範囲を出す():
    from mailauth.p7_aggregate.metrics import aggregate

    fact = parse_domain(DOMAIN, {"spf": [_spf_rec(["v=spf1 ip4:10.0.0.0/8 -all"])]})
    assert (fact["spf_ip4_addresses"], fact["spf_ip4_widest_prefix"]) == (2**24, 8)
    assert parse_domain(DOMAIN, {})["spf_ip4_addresses"] is None
    rows = [_row(1, spf_ip4_addresses=2**24), _row(2, spf_ip4_addresses=0),
            _row(3, spf_ip4_addresses=65536)]
    s = aggregate(rows, measured_month=dt.date(2026, 12, 1), population_id="jp-all-listed",
                  total_entities=3)
    assert s.spf_ip4_broad_domains == 1
    old = aggregate([_row(1)], measured_month=dt.date(2026, 12, 1),
                    population_id="jp-all-listed", total_entities=1)
    assert old.spf_ip4_broad_domains is None
