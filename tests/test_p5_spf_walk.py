"""SPF を include と redirect の先まで数える（BACKLOG 41）。

RFC 7208 §4.6.4 の上限 10 は評価の全体で1つで、include の先で数え直さない。
apex のレコードだけ数えると、上限を超えるドメインを見落とす。

テストは一切ネットワークに出ない。先の TXT は辞書で渡す。
"""

from __future__ import annotations

import datetime as dt

from mailauth.p5_parse.spf import MAX_LOOKUPS, walk_lookups
from mailauth.resolver import StaticResolver, make_answer


def _fetch(table: dict[str, list[str] | None]):
    calls: list[str] = []

    def fetch(name: str) -> list[str] | None:
        calls.append(name)
        return table.get(name, [])

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def test_先の中の項も数える():
    table = {
        "_spf.a.example": ["v=spf1 include:_spf2.a.example a mx -all"],
        "_spf2.a.example": ["v=spf1 ip4:192.0.2.0/24 -all"],
        "_spf.b.example": ["v=spf1 exists:%{i}.x.b.example -all"],
    }
    walk = walk_lookups(
        "v=spf1 include:_spf.a.example include:_spf.b.example mx -all", _fetch(table)
    )
    # apex 3（include 2 + mx）、a の先 3（include + a + mx）、b の先 1（exists）
    assert walk.lookups == 7
    assert walk.complete is True
    assert walk.exceeds is False
    assert walk.broken_target is False


def test_apex_だけなら3でも先まで数えると上限を超える():
    table = {f"_spf{i}.example": [
        "v=spf1 include:_n1.example include:_n2.example include:_n3.example -all"
    ] for i in range(3)}
    for n in range(1, 4):
        table[f"_n{n}.example"] = ["v=spf1 ip4:192.0.2.1 -all"]
    walk = walk_lookups(
        "v=spf1 include:_spf0.example include:_spf1.example include:_spf2.example -all",
        _fetch(table),
    )
    assert walk.lookups > MAX_LOOKUPS
    assert walk.exceeds is True


def test_include_の輪でも止まる():
    table = {
        "a.example": ["v=spf1 include:b.example -all"],
        "b.example": ["v=spf1 include:a.example -all"],
    }
    fetch = _fetch(table)
    walk = walk_lookups("v=spf1 include:a.example -all", fetch)
    assert walk.exceeds is True
    assert len(fetch.calls) <= MAX_LOOKUPS + 1


def test_redirect_は_all_が無いときだけ数える():
    table = {"_spf.example": ["v=spf1 a mx -all"]}
    walk = walk_lookups("v=spf1 redirect=_spf.example", _fetch(table))
    assert walk.lookups == 3
    # all があれば redirect は効かない（RFC 7208 §6.1）。先も引かない
    fetch = _fetch(table)
    walk = walk_lookups("v=spf1 -all redirect=_spf.example", fetch)
    assert (walk.lookups, fetch.calls) == (0, [])


def test_先に_SPF_が無いと壊れていると数える():
    table = {"gone.example": [], "two.example": ["v=spf1 -all", "v=spf1 ~all"]}
    walk = walk_lookups("v=spf1 include:gone.example include:two.example -all", _fetch(table))
    assert walk.missing == ["gone.example"]
    assert walk.multiple == ["two.example"]
    assert walk.broken_target is True


def test_引けなかった先があると分からないと言う():
    """**引けなかったものを「超えない」にしない**（原則5）。"""
    walk = walk_lookups("v=spf1 include:down.example -all", _fetch({"down.example": None}))
    assert walk.complete is False
    assert walk.exceeds is None
    assert walk.broken_target is None
    assert walk.unobserved == ["down.example"]


def test_マクロの先は引かず_分からないと言う():
    fetch = _fetch({})
    walk = walk_lookups("v=spf1 include:%{i}._spf.example -all", fetch)
    assert walk.macro == ["%{i}._spf.example"]
    assert walk.exceeds is None
    assert fetch.calls == []


def test_分からない先があっても上限を超えたと分かれば超えたと言う():
    many = " ".join(f"a:h{i}.example" for i in range(11))
    walk = walk_lookups(f"v=spf1 include:%{{i}}.x.example {many} -all", _fetch({}))
    assert walk.exceeds is True


# -- P5 の fact に出す ---------------------------------------------------------


def _fact(apex_spf: str, answers: dict):
    from mailauth.p5_parse.runner import parse_domain

    domain = "example-co.jp"
    by_purpose = {
        "spf": [{
            "domain": domain, "purpose": "spf", "query_name": domain,
            "observed": True, "record_present": True,
            "answers": [{"data": [apex_spf]}],
        }]
    }
    resolver = StaticResolver({
        (name, "TXT"): make_answer(name, "TXT", values) for name, values in answers.items()
    })
    return parse_domain(domain, by_purpose, resolver=resolver), resolver


def test_fact_に先まで数えた数と上限超えを出す():
    ten = " ".join(f"a:h{i}.example" for i in range(10))
    fact, _ = _fact(
        "v=spf1 include:_spf.vendor.example -all",
        {"_spf.vendor.example": [f"v=spf1 {ten} -all"]},
    )
    assert fact["spf_lookup_count"] == 1  # apex だけの数はそのまま
    assert fact["spf_lookup_total"] == 11
    assert fact["spf_total_exceeds"] is True
    assert fact["spf_target_broken"] is False


def test_fact_は先に_SPF_が無いものを出す():
    fact, _ = _fact("v=spf1 include:_spf.gone.example -all", {})
    assert fact["spf_target_broken"] is True
    assert fact["spf_total_exceeds"] is False


def test_resolver_が無ければ_null():
    from mailauth.p5_parse.runner import parse_domain

    fact = parse_domain("example-co.jp", {
        "spf": [{
            "domain": "example-co.jp", "purpose": "spf", "query_name": "example-co.jp",
            "observed": True, "record_present": True,
            "answers": [{"data": ["v=spf1 include:_spf.vendor.example -all"]}],
        }]
    })
    assert (fact["spf_lookup_total"], fact["spf_total_exceeds"], fact["spf_target_broken"]) == (
        None, None, None,
    )


def test_引けなかった先は_fact_で_null():
    from mailauth.p5_parse.runner import parse_domain

    domain = "example-co.jp"
    resolver = StaticResolver({
        ("_spf.down.example", "TXT"): make_answer("_spf.down.example", "TXT", observed=False,
                                                  rcode="SERVFAIL"),
    })
    fact = parse_domain(domain, {
        "spf": [{
            "domain": domain, "purpose": "spf", "query_name": domain,
            "observed": True, "record_present": True,
            "answers": [{"data": ["v=spf1 include:_spf.down.example -all"]}],
        }]
    }, resolver=resolver)
    assert (fact["spf_lookup_total"], fact["spf_total_exceeds"], fact["spf_target_broken"]) == (
        None, None, None,
    )


# -- P7 で数える -----------------------------------------------------------------


def test_集計は先まで数えて超えたものだけ_分からないものは数えない():
    from mailauth.p7_aggregate.metrics import DomainRow, aggregate

    def row(i, **kw):
        base = {"domain_id": f"d:{i}", "entity_id": f"jp:{i}", "is_apex": True,
                "observed": True, "spf_present": True}
        base.update(kw)
        return DomainRow.from_fact(base)

    rows = [
        row(1, spf_total_exceeds=True, spf_target_broken=False),
        row(2, spf_total_exceeds=False, spf_target_broken=True),
        row(3, spf_total_exceeds=None, spf_target_broken=None),
        row(4, spf_total_exceeds=float("nan"), spf_target_broken=False),
        # サブドメインは分母に入れない
        row(5, is_apex=False, spf_total_exceeds=True),
    ]
    stats = aggregate(rows, measured_month=dt.date(2026, 12, 1), population_id="jp-all-listed",
                      total_entities=5)
    assert stats.spf_lookup_exceeded_domains == 1
    assert stats.spf_target_broken_domains == 1


def test_先をたどっていない_run_は_0_ではなく_null():
    from mailauth.p7_aggregate.metrics import DomainRow, aggregate

    rows = [DomainRow.from_fact({"domain_id": "d:1", "entity_id": "jp:1", "is_apex": True,
                                 "observed": True, "spf_present": True})]
    stats = aggregate(rows, measured_month=dt.date(2026, 12, 1), population_id="jp-all-listed",
                      total_entities=1)
    assert stats.spf_lookup_exceeded_domains is None
    assert stats.spf_target_broken_domains is None


# -- 最後に効く all（redirect の先） ------------------------------------------------


def test_all_が無く_redirect_なら任せた先の_all():
    from mailauth.p5_parse.spf import effective_all

    table = {"x._spf._d.mim.ec": ["v=spf1 ip4:192.0.2.0/24 -all"]}
    assert effective_all("v=spf1 ip4:198.51.100.1 redirect=x._spf._d.mim.ec", _fetch(table)) == (
        "-", True,
    )


def test_自分に_all_があれば先は引かない():
    from mailauth.p5_parse.spf import effective_all

    fetch = _fetch({})
    assert effective_all("v=spf1 ~all redirect=other.example", fetch) == ("~", True)
    assert fetch.calls == []


def test_redirect_も_all_も無ければ本当に無い():
    from mailauth.p5_parse.spf import effective_all

    assert effective_all("v=spf1 ip4:192.0.2.1", _fetch({})) == (None, True)


def test_先を引けない_先に_SPF_が無いときは分からない():
    from mailauth.p5_parse.spf import effective_all

    assert effective_all("v=spf1 redirect=down.example", _fetch({"down.example": None})) == (
        None, False,
    )
    assert effective_all("v=spf1 redirect=gone.example", _fetch({})) == (None, False)
    assert effective_all("v=spf1 redirect=%{d}.x.example", _fetch({})) == (None, False)


def test_redirect_の輪でも止まる():
    from mailauth.p5_parse.spf import effective_all

    table = {"a.example": ["v=spf1 redirect=b.example"], "b.example": ["v=spf1 redirect=a.example"]}
    assert effective_all("v=spf1 redirect=a.example", _fetch(table)) == (None, False)


def test_fact_に最後に効く_all_を出す():
    fact, _ = _fact("v=spf1 redirect=_spf.vendor.example", {"_spf.vendor.example": ["v=spf1 -all"]})
    assert fact["spf_all_qualifier"] is None  # 自分のレコードの all はそのまま
    assert fact["spf_all_effective"] == "-"
    assert fact["spf_all_unknown"] is False


def test_集計は最後に効く_all_で数え_分からないものは別に数える():
    from mailauth.p7_aggregate.metrics import DomainRow, aggregate

    def row(i, **kw):
        base = {"domain_id": f"d:{i}", "entity_id": f"jp:{i}", "is_apex": True,
                "observed": True, "spf_present": True}
        base.update(kw)
        return DomainRow.from_fact(base)

    rows = [
        row(1, spf_all_qualifier=None, spf_all_effective="-", spf_all_unknown=False),
        row(2, spf_all_qualifier=None, spf_all_effective=None, spf_all_unknown=True),
        row(3, spf_all_qualifier=None, spf_all_effective=None, spf_all_unknown=False),
        # 列の無い過去の run は自分のレコードの all
        row(4, spf_all_qualifier="~"),
    ]
    s = aggregate(rows, measured_month=dt.date(2026, 12, 1), population_id="jp-all-listed",
                  total_entities=4)
    assert (s.spf_all_fail_domains, s.spf_all_softfail_domains, s.spf_all_missing_domains,
            s.spf_all_unknown_domains) == (1, 1, 1, 1)
