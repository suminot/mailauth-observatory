"""P2 ドメイン候補生成 と P3 メールドメイン確定。

テストは一切ネットワークに出ない。DNS と crt.sh は注入で置き換える。
"""

from __future__ import annotations

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from mailauth.contracts import ENTITY_ARROW_SCHEMA, Confidence, DomainRole, MeasureTier
from mailauth.ctlog import StaticCtSource, _extract
from mailauth.io import read_parquet
from mailauth.p1_population import run as run_p1
from mailauth.p2_candidates import MissingInputError as P2MissingInput
from mailauth.p2_candidates import run as run_p2
from mailauth.p2_candidates.runner import (
    candidate_id,
    load_report_vendor_patterns,
    match_report_vendor,
)
from mailauth.p3_domains import MissingInputError as P3MissingInput
from mailauth.p3_domains import run as run_p3
from mailauth.p3_domains.classify import (
    Probe,
    assign_tier,
    classify_confidence,
    classify_role,
    evidence_count,
)
from mailauth.paths import phase_output
from mailauth.resolver import StaticResolver, make_answer, shuffled

RUN = "2026-08"

#: 3社に起点ドメインを与える。gBizINFO が無い環境でも P2 を検証できるように
SEED_DOMAINS = {
    "jp:1234567890123": "sample-info.co.jp",
    "jp:2234567890123": "sample-motor.co.jp",
    "jp:3234567890123": "sample-bank.co.jp",
}

ANSWERS = {
    # 送信している主ドメイン（M365）
    ("sample-info.co.jp", "MX"): make_answer(
        "sample-info.co.jp", "MX", ["sample-info-co-jp.mail.protection.outlook.com."]
    ),
    ("sample-info.co.jp", "TXT"): make_answer(
        "sample-info.co.jp", "TXT", ["v=spf1 include:spf.protection.outlook.com -all"]
    ),
    ("_dmarc.sample-info.co.jp", "TXT"): make_answer(
        "_dmarc.sample-info.co.jp",
        "TXT",
        ["v=DMARC1; p=reject; rua=mailto:agg@sample-info.co.jp,mailto:x@dmarc25.jp"],
    ),
    # 配信専用の関連ドメイン
    ("sample-info-mail.jp", "MX"): make_answer("sample-info-mail.jp", "MX", ["mx.sample.jp."]),
    ("sample-info-mail.jp", "TXT"): make_answer(
        "sample-info-mail.jp", "TXT", ["v=spf1 include:amazonses.com ~all"]
    ),
    # 模範的に固められたパークドメイン（Null MX + -all + p=reject）
    ("cdn-sample.net", "MX"): make_answer("cdn-sample.net", "MX", ["."]),
    ("cdn-sample.net", "TXT"): make_answer("cdn-sample.net", "TXT", ["v=spf1 -all"]),
    ("_dmarc.cdn-sample.net", "TXT"): make_answer(
        "_dmarc.cdn-sample.net", "TXT", ["v=DMARC1; p=reject; sp=reject"]
    ),
    # SPF redirect で別ドメインを指す
    ("sample-motor.co.jp", "MX"): make_answer("sample-motor.co.jp", "MX", ["mx.iphmx.com."]),
    ("sample-motor.co.jp", "TXT"): make_answer(
        "sample-motor.co.jp", "TXT", ["v=spf1 redirect=_spf.sample-motor-group.jp"]
    ),
    # MX 無し + -all → 意図的な送信禁止
    ("sample-bank.co.jp", "TXT"): make_answer("sample-bank.co.jp", "TXT", ["v=spf1 -all"]),
}

CT = StaticCtSource(
    {
        "sample-info.co.jp": ["sample-info.co.jp", "sample-info-mail.jp", "cdn-sample.net"],
        "sample-motor.co.jp": ["sample-motor.co.jp"],
    }
)


@pytest.fixture
def seeded_run(edinet_sample):
    """P1 を回し、3社に official_domain を与えた状態を作る。"""
    run_p1(
        config="configs/populations/jp-all-listed.yaml",
        run_id=RUN,
        source_file=str(edinet_sample),
    )
    path = phase_output(RUN, "p1_population", "entities.parquet")
    df = pd.read_parquet(path)
    df["official_domain"] = df["entity_id"].map(SEED_DOMAINS)
    df["official_url"] = df["official_domain"].apply(
        lambda d: f"https://www.{d}" if isinstance(d, str) else None
    )
    pq.write_table(
        pa.Table.from_pandas(df, schema=ENTITY_ARROW_SCHEMA, preserve_index=False),
        path,
        compression="zstd",
    )
    return path


def _run_p2(**kw):
    return run_p2(run_id=RUN, resolver=StaticResolver(ANSWERS), ct_source=CT, **kw)


def _run_p3(**kw):
    return run_p3(run_id=RUN, resolver=StaticResolver(ANSWERS), **kw)


def candidates() -> pd.DataFrame:
    df = read_parquet(phase_output(RUN, "p2_candidates", "domain_candidates.parquet"))
    assert df is not None
    return df


def domains() -> pd.DataFrame:
    df = read_parquet(phase_output(RUN, "p3_domains", "domains.parquet"))
    assert df is not None
    return df


# ===========================================================================
# P2
# ===========================================================================


def test_p2_requires_p1_output():
    """前工程が無ければ空の結果を作らず理由を添えて止める。"""
    with pytest.raises(P2MissingInput, match="p1-population"):
        _run_p2()


def test_p2_uses_all_four_discovery_paths(seeded_run):
    result = _run_p2()
    assert result["status"] == "success"
    methods = result["breakdown"]["by_discovery_method"]
    assert methods["official_url"] == 3
    assert methods["ct_log"] >= 3
    assert methods["spf_redirect"] == 1  # sample-motor の redirect
    assert methods["dmarc_rua"] == 1  # 自社ドメイン宛の rua のみ


def test_p2_excludes_third_party_report_vendors(seeded_run):
    """rua が第三者サービスなら、その企業のドメインではない。

    dmarc25.jp を候補に入れると数百社に紐づき、他社のドメインを計測してしまう。
    """
    result = _run_p2()
    assert "dmarc25.jp" not in set(candidates()["domain"])
    assert result["breakdown"]["dmarc_report_vendors"] == {"TwoFive": 1}


def test_p2_normalizes_to_etld_plus_one(seeded_run):
    _run_p2()
    for domain in candidates()["domain"]:
        # サブドメインが残っていたら候補が爆発する
        assert not domain.startswith("www.")
        assert domain == domain.lower()


def test_p2_records_same_domain_from_multiple_paths(seeded_run):
    """同じドメインが複数経路で見つかったら、経路ごとに行を残す。

    確度判定で「独立した裏付けが何件あるか」を数えるため。
    """
    _run_p2()
    df = candidates()
    rows = df[df["domain"] == "sample-info.co.jp"]
    assert set(rows["discovery_method"]) >= {"official_url", "ct_log"}


def test_p2_reports_entities_with_zero_candidates(seeded_run):
    """起点ドメインが無い企業は候補ゼロ。黙って消さず数える。"""
    result = _run_p2()
    # 17社のうち起点を与えたのは3社
    assert result["breakdown"]["entities_with_zero_candidates"] == 14
    assert result["counts"]["success"] == 3


def test_p2_warns_when_no_seed_domains_at_all(edinet_sample):
    """gBizINFO トークンが無いと起点が1件も無い。それを大きく警告する。"""
    run_p1(
        config="configs/populations/jp-all-listed.yaml",
        run_id=RUN,
        source_file=str(edinet_sample),
    )
    result = _run_p2()
    codes = {w["code"] for w in result["warnings"]}
    assert "NO_SEED_DOMAINS" in codes
    message = next(w["message"] for w in result["warnings"] if w["code"] == "NO_SEED_DOMAINS")
    assert "MAILAUTH_GBIZINFO_TOKEN" in message


def test_p2_manual_dictionary(seeded_run, tmp_path, monkeypatch):
    """人手でグループ会社を追加できる経路を必ず用意する（DESIGN.md P2）。"""
    import yaml

    from mailauth import paths

    manual = tmp_path / "manual.csv"
    manual.write_text(
        "entity_id,domain,note,added\n"
        "jp:1234567890123,sample-group.co.jp,IRサイトに記載,2026-08-03\n",
        encoding="utf-8",
    )
    base = yaml.safe_load(
        (paths.repo_root() / "configs/candidates.yaml").read_text(encoding="utf-8")
    )
    base["manual_domains"] = str(manual)
    cfg = tmp_path / "candidates.yaml"
    cfg.write_text(yaml.safe_dump(base, allow_unicode=True), encoding="utf-8")

    _run_p2(config=str(cfg))
    df = candidates()
    row = df[df["domain"] == "sample-group.co.jp"]
    assert len(row) == 1
    assert row.iloc[0]["discovery_method"] == "manual"
    assert row.iloc[0]["source_detail"] == "IRサイトに記載"


def test_p2_is_idempotent(seeded_run):
    _run_p2()
    path = phase_output(RUN, "p2_candidates", "domain_candidates.parquet")
    first = path.read_bytes()
    _run_p2()
    assert path.read_bytes() == first


def test_p2_dry_run_writes_nothing(seeded_run):
    result = _run_p2(dry_run=True)
    assert result["outputs"] == []
    assert not phase_output(RUN, "p2_candidates", "domain_candidates.parquet").exists()


def test_candidate_id_is_deterministic():
    a = candidate_id("jp:1", "example.jp", "ct_log")
    assert a == candidate_id("jp:1", "example.jp", "ct_log")
    assert a != candidate_id("jp:1", "example.jp", "manual")


def test_report_vendor_matching():
    patterns = load_report_vendor_patterns()
    assert match_report_vendor("dmarc25.jp", patterns) == "TwoFive"
    assert match_report_vendor("example.co.jp", patterns) is None


# -- CT ログの正規化 ---------------------------------------------------------


def test_ct_extract_collapses_to_apex_and_dedupes():
    """CT は1ドメインに数千件返すことがある。apex に丸めないと候補が爆発する。"""
    rows = [
        {"name_value": "www.example.co.jp\nmail.example.co.jp\n*.example.co.jp"},
        {"name_value": "shop.example.jp"},
        {"name_value": ""},
    ]
    result = _extract("example.co.jp", rows)
    assert result.found == ["example.co.jp", "example.jp"]
    assert result.raw_names == 4  # 正規化前の件数も記録する


# ===========================================================================
# P3 確度判定（システムの中核）
# ===========================================================================


def test_p3_requires_p2_output(seeded_run):
    with pytest.raises(P3MissingInput, match="p2-candidates"):
        _run_p3()


@pytest.mark.parametrize(
    ("mx", "spf_aligned", "dkim", "dmarc", "independent", "hard_deny", "expected"),
    [
        # MX実在 + From整合SPF + 独立裏付け → confirmed
        (True, True, False, True, 1, False, Confidence.CONFIRMED),
        (True, False, True, False, 2, False, Confidence.CONFIRMED),
        # 独立裏付けが無い → likely
        (True, True, False, False, 0, False, Confidence.LIKELY),
        # 一次証拠は無いが DMARC はある → likely
        (True, False, False, True, 1, False, Confidence.LIKELY),
        # MX はあるが何も無い → unknown
        (True, False, False, False, 1, False, Confidence.UNKNOWN),
        # MX 無し + SPF -all → 意図的な送信禁止。設定漏れではない
        (False, False, False, False, 1, True, Confidence.PARKED),
        # MX 無し + 宣言も無い → unknown
        (False, False, False, False, 1, False, Confidence.UNKNOWN),
        # MX 無しなら DMARC があっても parked/unknown に落ちる
        (False, True, True, True, 5, False, Confidence.UNKNOWN),
    ],
)
def test_classify_confidence(mx, spf_aligned, dkim, dmarc, independent, hard_deny, expected):
    assert (
        classify_confidence(mx, spf_aligned, dkim, dmarc, independent, hard_deny) == expected
    )


def test_classify_role():
    assert classify_role("a.jp", "a.jp", Confidence.CONFIRMED) == DomainRole.PRIMARY
    assert classify_role("b.jp", "a.jp", Confidence.CONFIRMED) == DomainRole.RELATED
    assert classify_role("a.jp", "a.jp", Confidence.PARKED) == DomainRole.PARKED
    assert classify_role("b.jp", None, Confidence.LIKELY) == DomainRole.RELATED


def test_assign_tier_keeps_full_measurement_for_sending_domains():
    """送信していないドメインに DKIM 50セレクタは投げない。作法が悪いし検出されない。"""
    assert assign_tier(Confidence.CONFIRMED) == MeasureTier.A
    assert assign_tier(Confidence.LIKELY) == MeasureTier.A
    assert assign_tier(Confidence.PARKED) == MeasureTier.C
    assert assign_tier(Confidence.UNKNOWN) == MeasureTier.C


def test_evidence_weights_follow_the_documented_priority():
    """DKIM CNAME >= MX > SPF include > 所有権確認TXT。"""
    dkim_only = Probe(domain="a.jp", dkim_found=True)
    mx_only = Probe(domain="a.jp", mx_exists=True)
    spf_only = Probe(domain="a.jp", spf_exists=True)
    assert evidence_count(dkim_only) > evidence_count(mx_only) > evidence_count(spf_only)


def test_probe_spf_deny_helpers():
    hard = Probe(domain="a.jp", spf_exists=True, spf_all_qualifier="-")
    soft = Probe(domain="a.jp", spf_exists=True, spf_all_qualifier="~")
    plus = Probe(domain="a.jp", spf_exists=True, spf_all_qualifier="+")
    assert hard.spf_hard_deny and hard.spf_soft_deny
    assert not soft.spf_hard_deny and soft.spf_soft_deny
    assert not plus.spf_hard_deny and not plus.spf_soft_deny


# -- P3 の通し ---------------------------------------------------------------


def test_p3_classifies_the_seeded_domains(seeded_run):
    _run_p2()
    result = _run_p3()
    assert result["status"] == "success"

    df = domains().set_index("domain")

    # 送信している主ドメイン
    info = df.loc["sample-info.co.jp"]
    assert info["confidence"] == Confidence.CONFIRMED
    assert info["domain_role"] == DomainRole.PRIMARY
    assert info["measure_tier"] == MeasureTier.A
    assert bool(info["mx_exists"]) and bool(info["spf_exists"]) and bool(info["dmarc_exists"])

    # CT から見つかった配信専用の関連ドメイン
    mail = df.loc["sample-info-mail.jp"]
    assert mail["confidence"] == Confidence.CONFIRMED
    assert mail["domain_role"] == DomainRole.RELATED

    # 模範的に固められたパークドメイン
    parked = df.loc["cdn-sample.net"]
    assert parked["confidence"] == Confidence.PARKED
    assert bool(parked["null_mx"]) is True
    assert bool(parked["spf_hard_deny"]) is True
    assert bool(parked["mx_exists"]) is False  # Null MX は MX 実在と見なさない
    assert parked["measure_tier"] == MeasureTier.C

    # MX 無し + -all。設定漏れではなく意図的な送信禁止
    bank = df.loc["sample-bank.co.jp"]
    assert bank["confidence"] == Confidence.PARKED
    assert bool(bank["spf_hard_deny"]) is True

    # SPF redirect で見つかっただけのドメイン。DNS 由来なので独立裏付けにならない
    group = df.loc["sample-motor-group.jp"]
    assert group["confidence"] == Confidence.UNKNOWN
    assert group["measure_tier"] == MeasureTier.C


def test_p3_evidence_is_recorded_for_traceability(seeded_run):
    """なぜこの判定になったかを追跡できること。"""
    import json

    _run_p2()
    _run_p3()
    row = domains().set_index("domain").loc["sample-info.co.jp"]
    evidence = json.loads(row["evidence"])
    types = {e["type"] for e in evidence}
    assert {"mx", "spf", "dmarc", "discovery"} <= types
    assert row["evidence_count"] > 0


def test_p3_breakdown_reports_the_distribution(seeded_run):
    _run_p2()
    result = _run_p3()
    b = result["breakdown"]
    assert b["by_confidence"][Confidence.CONFIRMED] == 3
    assert b["by_confidence"][Confidence.PARKED] == 2
    assert b["by_measure_tier"][MeasureTier.A] == 3
    assert b["null_mx_domains"] == 1
    assert b["spf_hard_deny_domains"] == 3
    assert "entities_with_no_confirmed_domain" in b


def test_p3_failed_probe_is_not_measured(seeded_run):
    """「取れなかった」を「無かった」として下流に渡さない（原則5）。"""
    from mailauth.resolver import DnsAnswer

    failing = dict(ANSWERS)
    failing[("sample-bank.co.jp", "MX")] = DnsAnswer(
        name="sample-bank.co.jp",
        rtype="MX",
        observed=False,
        record_present=None,
        rcode="SERVFAIL",
    )
    _run_p2()
    result = run_p3(run_id=RUN, resolver=StaticResolver(failing))

    row = domains().set_index("domain").loc["sample-bank.co.jp"]
    assert bool(row["is_measured"]) is False
    assert row["exclusion_reason"] == "primary_probe_failed"
    assert result["counts"]["failed"] >= 1
    assert "SERVFAIL" in result["failure_breakdown"]


def test_p3_acceptance_is_reported_not_enforced(seeded_run):
    _run_p2()
    result = _run_p3()
    acc = result["breakdown"]["acceptance"]
    assert "confirmed_rate" in acc
    assert "entities_without_confirmed_rate" in acc
    assert result["status"] == "success"


def test_p3_is_idempotent(seeded_run):
    _run_p2()
    _run_p3()
    path = phase_output(RUN, "p3_domains", "domains.parquet")
    first = path.read_bytes()
    _run_p3()
    assert path.read_bytes() == first


def test_p3_dry_run_writes_nothing(seeded_run):
    _run_p2()
    result = _run_p3(dry_run=True)
    assert result["outputs"] == []
    assert not phase_output(RUN, "p3_domains", "domains.parquet").exists()


# -- 作法 -------------------------------------------------------------------


def test_shuffle_is_reproducible_with_a_seed():
    """同一権威への連続クエリを避けつつ、原則6（冪等）を壊さない。"""
    items = [f"d{i}.jp" for i in range(20)]
    assert shuffled(items, 42) == shuffled(items, 42)
    assert shuffled(items, 42) != items


def test_static_resolver_returns_nodata_not_failure_for_unknown_names():
    """未登録は「レコードが無い」。「取れなかった」ではない（原則5）。"""
    answer = StaticResolver().query("nothing.example", "MX")
    assert answer.observed is True
    assert answer.record_present is False
    assert answer.rcode == "NODATA"


# -- TCP フォールバック（DESIGN.md P4 のステートマシン） ------------------------


def test_truncated_response_falls_back_to_tcp(monkeypatch):
    """UDP応答が truncated なら TCP に切り替える。

    TXT が多いドメインでは 512 バイトを超えるのが普通なので、
    ここを実装しないと大企業の SPF を一切観測できない。
    """
    import dns.flags
    import dns.message
    import dns.rdatatype

    from mailauth import resolver as resolver_mod

    truncated = dns.message.make_response(dns.message.make_query("big.example", "TXT"))
    truncated.flags |= dns.flags.TC

    full = dns.message.make_response(dns.message.make_query("big.example", "TXT"))
    full.answer.append(
        dns.rrset.from_text("big.example.", 300, "IN", "TXT", '"v=spf1 -all"')
    )

    calls = {"udp": 0, "tcp": 0}

    def fake_udp(query, where, timeout=None, **kw):
        calls["udp"] += 1
        return truncated

    def fake_tcp(query, where, timeout=None, **kw):
        calls["tcp"] += 1
        return full

    monkeypatch.setattr(resolver_mod.dns.query, "udp", fake_udp)
    monkeypatch.setattr(resolver_mod.dns.query, "tcp", fake_tcp)

    r = resolver_mod.DnsResolver(nameservers=["203.0.113.1"], qps=0)
    answer = r.query("big.example", "TXT")

    assert calls == {"udp": 1, "tcp": 1}
    assert answer.observed is True
    assert answer.record_present is True
    assert answer.values == ["v=spf1 -all"]
    assert r.stats["tcp_fallback"] == 1
    assert r.stats["tcp_failed"] == 0


def test_tcp_failure_is_reported_as_not_observed(monkeypatch):
    """TCP が通らない経路では「取れなかった」と記録する。

    「SPF が無い」と誤読させないことが要点（原則5）。
    """
    import dns.exception
    import dns.flags
    import dns.message

    from mailauth import resolver as resolver_mod

    truncated = dns.message.make_response(dns.message.make_query("big.example", "TXT"))
    truncated.flags |= dns.flags.TC

    monkeypatch.setattr(resolver_mod.dns.query, "udp", lambda *a, **k: truncated)
    monkeypatch.setattr(
        resolver_mod.dns.query,
        "tcp",
        lambda *a, **k: (_ for _ in ()).throw(dns.exception.Timeout()),
    )

    r = resolver_mod.DnsResolver(nameservers=["203.0.113.1"], qps=0)
    answer = r.query("big.example", "TXT")

    assert answer.observed is False
    assert answer.record_present is None  # 「不明」であって「無い」ではない
    assert answer.rcode == "TRUNCATED_TCP_UNAVAILABLE"
    assert "TCP/53" in answer.error
    assert r.stats["tcp_failed"] == 1


def test_nxdomain_and_nodata_are_observed_not_failures(monkeypatch):
    """NXDOMAIN と NODATA は確定結果。リトライもしない。"""
    import dns.message
    import dns.rcode

    from mailauth import resolver as resolver_mod

    nxdomain = dns.message.make_response(dns.message.make_query("gone.example", "MX"))
    nxdomain.set_rcode(dns.rcode.NXDOMAIN)
    nodata = dns.message.make_response(dns.message.make_query("empty.example", "MX"))

    responses = {"gone.example.": nxdomain, "empty.example.": nodata}
    monkeypatch.setattr(
        resolver_mod.dns.query,
        "udp",
        lambda q, *a, **k: responses[q.question[0].name.to_text()],
    )

    r = resolver_mod.DnsResolver(nameservers=["203.0.113.1"], qps=0, retries=3)
    for name, expected in [("gone.example", "NXDOMAIN"), ("empty.example", "NODATA")]:
        answer = r.query(name, "MX")
        assert answer.observed is True
        assert answer.record_present is False
        assert answer.rcode == expected
    assert r.stats["retries"] == 0  # 確定結果なのでリトライしない
    assert r.stats["failures"] == 0


def test_servfail_is_not_observed(monkeypatch):
    import dns.message
    import dns.rcode

    from mailauth import resolver as resolver_mod

    servfail = dns.message.make_response(dns.message.make_query("broken.example", "MX"))
    servfail.set_rcode(dns.rcode.SERVFAIL)
    monkeypatch.setattr(resolver_mod.dns.query, "udp", lambda *a, **k: servfail)

    answer = resolver_mod.DnsResolver(nameservers=["203.0.113.1"], qps=0).query(
        "broken.example", "MX"
    )
    assert answer.observed is False
    assert answer.record_present is None
    assert answer.rcode == "SERVFAIL"


def test_resolver_caches_repeated_names(monkeypatch):
    """同じ名前を二度引かない。権威DNSへの負荷回避は倫理的義務でもある。"""
    import dns.message

    from mailauth import resolver as resolver_mod

    resp = dns.message.make_response(dns.message.make_query("a.example", "MX"))
    calls = {"n": 0}

    def counted(*a, **k):
        calls["n"] += 1
        return resp

    monkeypatch.setattr(resolver_mod.dns.query, "udp", counted)
    r = resolver_mod.DnsResolver(nameservers=["203.0.113.1"], qps=0)
    r.query("a.example", "MX")
    r.query("A.EXAMPLE.", "MX")  # 大文字・末尾ドットでも同一視する
    assert calls["n"] == 1
    assert r.stats["cache_hits"] == 1


def test_p2_discovered_at_is_derived_from_the_run_not_the_clock(seeded_run):
    """壁時計を埋めると同じ入力でも出力が変わり、原則6（冪等）が壊れる。

    秒境界をまたいでも出力がバイト単位で一致すること。
    """
    import time

    _run_p2()
    path = phase_output(RUN, "p2_candidates", "domain_candidates.parquet")
    first = path.read_bytes()
    time.sleep(1.1)  # 秒境界を確実にまたぐ
    _run_p2()
    assert path.read_bytes() == first

    # discovered_at は run の月initialになっている
    df = candidates()
    stamps = {str(v) for v in df["discovered_at"]}
    assert len(stamps) == 1
    assert stamps.pop().startswith("2026-08-01 00:00:00")


# ===========================================================================
# CT ログのキャッシュは月で区切る
# ===========================================================================


class _CountingCtTransport:
    """crt.sh を叩いた回数を数える。ネットワークには出ない。"""

    def __init__(self, names: list[str]):
        self.calls = 0
        self._names = names

    def handle(self, request):
        import httpx as _httpx

        self.calls += 1
        return _httpx.Response(
            200, json=[{"name_value": "\n".join(self._names)}]
        )


def _ct_client(tmp_path, month, transport):
    import httpx as _httpx

    from mailauth.ctlog import CrtShClient

    return CrtShClient(
        cache_dir=tmp_path / "crtsh",
        month=month,
        qps=0,
        client=_httpx.Client(transport=_httpx.MockTransport(transport.handle)),
    )


def test_the_ct_cache_is_reused_within_the_same_month(tmp_path):
    """同じ月の再実行はキャッシュを使う（原則6 冪等・crt.sh に再負荷をかけない）。"""
    t = _CountingCtTransport(["mail.example.jp"])
    first = _ct_client(tmp_path, "2026-08", t).search("example.jp")
    second = _ct_client(tmp_path, "2026-08", t).search("example.jp")

    assert t.calls == 1, "同じ月で2回叩いている"
    assert first.from_cache is False
    assert second.from_cache is True
    assert second.cache_month == "2026-08"
    assert second.found == first.found


def test_the_ct_cache_is_not_reused_across_months(tmp_path):
    """**先月の応答を今月の観測として使わない。**

    CT ログは追記されていく。月を跨いで使い回すと、その間に発行された
    証明書が永久に見えず、**候補生成が初月の状態で凍結する。**
    しかも数字が動かないだけなので気付けない。
    """
    t = _CountingCtTransport(["mail.example.jp"])
    _ct_client(tmp_path, "2026-08", t).search("example.jp")
    assert t.calls == 1

    # 翌月に新しい証明書が出たとする
    t._names = ["mail.example.jp", "newbrand.example.net"]
    later = _ct_client(tmp_path, "2026-09", t).search("example.jp")

    assert t.calls == 2, "翌月なのに取り直していない"
    assert later.from_cache is False
    # found は eTLD+1 に畳まれているので newbrand.example.net は example.net になる
    assert "example.net" in later.found, "翌月に出た証明書を拾えていない"


def test_the_ct_cache_records_which_month_it_came_from(tmp_path):
    """payload にも月を書く。ディレクトリを動かしても月が分かるように。"""
    import json as _json

    t = _CountingCtTransport(["mail.example.jp"])
    _ct_client(tmp_path, "2026-08", t).search("example.jp")
    path = tmp_path / "crtsh" / "month=2026-08" / "example.jp.json"
    assert path.is_file(), "月ごとのディレクトリに置かれていない"
    assert _json.loads(path.read_text(encoding="utf-8"))["month"] == "2026-08"


def test_p2_scopes_the_ct_cache_to_the_run(seeded_run):
    """**月次計測の経路では必ず月を渡す。** 渡さないと時系列が凍結する。"""
    from mailauth.manifest import read_manifest
    from mailauth.paths import phase_dir

    _run_p2()
    manifest = read_manifest(phase_dir(RUN, "p2_candidates"))
    ct = manifest["breakdown"]["ct"]
    assert ct["cache_month"] == RUN
    # 当月以外のキャッシュを「今月の観測」として数えていない
    assert ct["stale_cache"] == 0


def test_p2_passes_the_run_id_as_the_cache_month():
    """実装が month を渡していること（注入した ct_source では通らない経路）。"""
    import inspect

    from mailauth.p2_candidates import runner as p2

    source = inspect.getsource(p2.run)
    assert "month=run_id" in source, "CrtShClient に月を渡していない"


# ===========================================================================
# rua 宛先は自社ドメインのときだけ候補にする
# ===========================================================================


def _rua_answers(company: str, rua_domain: str):
    """company の _dmarc が rua_domain 宛のレポートを要求している状態。"""
    from mailauth.resolver import make_answer

    return {
        (company, "TXT"): make_answer(company, "TXT", ["v=spf1 -all"]),
        (f"_dmarc.{company}", "TXT"): make_answer(
            f"_dmarc.{company}",
            "TXT",
            [f"v=DMARC1; p=none; rua=mailto:abc123@{rua_domain}"],
        ),
    }


def test_a_third_party_rua_target_is_not_a_candidate(seeded_run):
    """**他社のドメインをその企業の送信ドメインとして公開しない。**

    「example の rua が vendor.jp を指している」が示すのは「vendor.jp が
    example のレポートを受け取る」ことだけで、example が vendor.jp を
    所有している証拠にはならない。

    実測（2026-08）では securemx.jp が keyence.co.jp のドメインとして
    confidence=likely まで通っていた。レポート処理サービス自身が
    MX/SPF/DMARC を持っているため、ドメイン単体の実証では見分けが付かない。
    """
    from mailauth.resolver import StaticResolver

    answers = _rua_answers("sample-info.co.jp", "reports.vendor-example.jp")
    result = run_p2(run_id=RUN, resolver=StaticResolver(answers), ct_source=CT)

    assert "vendor-example.jp" not in set(candidates()["domain"])
    targets = result["breakdown"]["unaligned_rua_targets"]
    assert "vendor-example.jp" in targets
    assert "sample-info.co.jp" in targets["vendor-example.jp"]
    assert any(w["code"] == "UNALIGNED_RUA_TARGET" for w in result["warnings"])


def test_a_self_addressed_rua_target_is_still_a_candidate(seeded_run):
    """自社ドメイン宛の rua は候補にする（仕様どおり）。"""
    from mailauth.resolver import StaticResolver

    answers = _rua_answers("sample-info.co.jp", "dmarc.sample-info.co.jp")
    result = run_p2(run_id=RUN, resolver=StaticResolver(answers), ct_source=CT)

    rows = candidates()
    rua = rows[rows["discovery_method"] == "dmarc_rua"]
    assert "sample-info.co.jp" in set(rua["domain"])
    assert result["breakdown"]["unaligned_rua_targets"] == {}


def test_a_known_vendor_is_counted_but_not_added(seeded_run):
    """辞書にあるベンダーは候補にせず、P6 の材料として件数だけ残す。"""
    from mailauth.resolver import StaticResolver

    answers = _rua_answers("sample-info.co.jp", "rua.powerdmarc.com")
    result = run_p2(run_id=RUN, resolver=StaticResolver(answers), ct_source=CT)

    assert "powerdmarc.com" not in set(candidates()["domain"])
    assert result["breakdown"]["dmarc_report_vendors"].get("PowerDMARC") == 1
    # 辞書で判別できたものは同定の作業リストに出さない
    assert "powerdmarc.com" not in result["breakdown"]["unaligned_rua_targets"]


def test_a_dns_only_domain_shared_by_two_entities_is_dropped(seeded_run):
    """**同じ基盤を複数社が指していたら、その全社の所有ではない。**

    ESP の redirect 先のように、DNS から辿っただけの経路で複数社に
    共用されているドメインは他社の基盤である。辞書に無くても落とせる。
    """
    from mailauth.resolver import StaticResolver, make_answer

    shared = "esp-example.net"
    answers = {}
    for company in ("sample-info.co.jp", "sample-motor.co.jp"):
        answers[(company, "TXT")] = make_answer(
            company, "TXT", [f"v=spf1 redirect=_spf.{shared}"]
        )
    result = run_p2(run_id=RUN, resolver=StaticResolver(answers), ct_source=CT)

    assert shared not in set(candidates()["domain"])
    dropped = result["breakdown"]["shared_rua_dropped"]
    assert shared in dropped
    assert len(dropped[shared]) == 2
    assert any(w["code"] == "SHARED_RUA_DOMAIN_DROPPED" for w in result["warnings"])


def test_a_shared_domain_with_ownership_evidence_is_kept(seeded_run):
    """official_url の裏付けがあるドメインは残す。本物のグループ共用がある。"""
    from mailauth.resolver import StaticResolver, make_answer

    # 2社の redirect 先が、1社目の公式ドメインそのものだった場合
    answers = {
        ("sample-motor.co.jp", "TXT"): make_answer(
            "sample-motor.co.jp", "TXT", ["v=spf1 redirect=_spf.sample-info.co.jp"]
        ),
    }
    run_p2(run_id=RUN, resolver=StaticResolver(answers), ct_source=CT)
    rows = candidates()
    methods = set(rows[rows["domain"] == "sample-info.co.jp"]["discovery_method"])
    # official_url の裏付けがあるので落ちない
    assert "official_url" in methods


def test_the_docstring_rule_matches_the_implementation():
    """仕様に「自社ドメインなら」と書いたなら、実装がそれを確かめること。

    キャッシュの月スコープと同じで、**書いてあるのに実装が伴っていない**のが
    この種のバグの入り口だった。
    """
    import inspect

    from mailauth.p2_candidates import runner as p2

    assert "rua 宛先が自社ドメインなら候補に" in (p2.__doc__ or "")
    source = inspect.getsource(p2._discover_from_dns)
    assert "etld_plus_one(domain) != etld_plus_one(apex)" in source
