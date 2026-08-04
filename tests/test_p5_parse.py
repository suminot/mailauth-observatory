"""P5 パース。仕様に照らした解釈を検証する。"""

from __future__ import annotations

import pandas as pd
import pytest

from mailauth.contracts import (
    DOMAIN_ARROW_SCHEMA,
    DkimStatus,
    MeasureTier,
    PolicyLabel,
    SpecVersion,
)
from mailauth.io import write_parquet
from mailauth.p4_measure import run as run_p4
from mailauth.p5_parse import MissingInputError
from mailauth.p5_parse import dkim as dkim_mod
from mailauth.p5_parse import dmarc as dmarc_mod
from mailauth.p5_parse import extras as extras_mod
from mailauth.p5_parse import run as run_p5
from mailauth.p5_parse import spf as spf_mod
from mailauth.p5_parse.orgdomain import resolve as resolve_org
from mailauth.p5_parse.orgdomain import tree_walk_names
from mailauth.paths import phase_output
from mailauth.resolver import DnsAnswer, StaticResolver, make_answer

RUN = "2026-08"


# ===========================================================================
# SPF
# ===========================================================================


def test_multiple_spf_records_is_permerror():
    """複数の v=spf1 は PermError。どちらを採るかの問題ではない（RFC 7208 §4.5）。"""
    result = spf_mod.parse(["v=spf1 -all", "v=spf1 ~all"])
    assert result.present is True
    assert result.valid is False
    assert result.error == spf_mod.SpfError.MULTIPLE_RECORDS


def test_no_spf_record_is_not_an_error():
    result = spf_mod.parse(["google-site-verification=x"])
    assert result.present is False
    assert result.error is None


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        # カウント対象: include, a, mx, ptr, exists, redirect
        ("v=spf1 include:a.example -all", 1),
        ("v=spf1 include:a.example include:b.example mx a -all", 4),
        ("v=spf1 redirect=x.example", 1),
        # 非対象: all, ip4, ip6
        ("v=spf1 ip4:198.51.100.0/24 ip6:2001:db8::/32 -all", 0),
        ("v=spf1 exists:%{i}.example ptr -all", 2),
    ],
)
def test_lookup_counting(record, expected):
    """「ルックアップを行うメカニズムの数」であり生成クエリ総数ではない。"""
    assert spf_mod.count_lookups(record) == expected


def test_exceeding_ten_lookups_is_permerror():
    includes = " ".join(f"include:x{i}.example" for i in range(11))
    result = spf_mod.parse([f"v=spf1 {includes} -all"])
    assert result.lookup_count == 11
    assert result.exceeds_limit is True
    assert result.valid is False
    assert result.error == spf_mod.SpfError.PERMERROR


def test_exactly_ten_lookups_is_valid():
    includes = " ".join(f"include:x{i}.example" for i in range(10))
    result = spf_mod.parse([f"v=spf1 {includes} -all"])
    assert result.lookup_count == 10
    assert result.exceeds_limit is False
    assert result.valid is True


def test_flattening_is_detected():
    """10ルックアップ制限を回避するために include を IP に展開する運用がある。"""
    ips = " ".join(f"ip4:198.51.100.{i}" for i in range(1, 40))
    result = spf_mod.parse([f"v=spf1 {ips} -all"])
    assert result.is_flattened is True
    assert result.ip4_count == 39


def test_dynamic_spf_is_detected():
    """Valimail の動的SPF。静的にルックアップ数を数えても実効を表さない。"""
    record = "v=spf1 include:%{i}._ip.%{h}._ehlo.%{d}._spf.vali.email -all"
    result = spf_mod.parse([record])
    assert result.is_dynamic is True


def test_split_spf_is_joined_before_parsing():
    """255バイト境界の分割。連結してから解釈しないと取りこぼす。"""
    from mailauth.records import join_txt_strings

    joined = join_txt_strings(["v=spf1 include:_spf.example.com ", "-all"])
    result = spf_mod.parse([joined])
    assert result.valid is True
    assert result.all_qualifier == "-"
    assert result.includes == ["_spf.example.com"]


# ===========================================================================
# DMARC 二重計算
# ===========================================================================


@pytest.mark.parametrize(
    ("p", "pct", "t", "has_rua", "eff_7489", "eff_9989", "label"),
    [
        # DESIGN.md P5「ポリシー強度の分類ラベル」の表をそのまま検証する
        ("reject", None, None, True, "reject", "reject", PolicyLabel.ENFORCED_REJECT),
        ("reject", 10, None, True, "quarantine", "reject", PolicyLabel.NOMINAL_REJECT_WEAK_PCT),
        ("reject", None, "y", True, "reject", "quarantine", PolicyLabel.NOMINAL_REJECT_TESTING),
        ("reject", None, None, False, "reject", "reject", PolicyLabel.BLIND_REJECT),
        (
            "quarantine",
            50,
            None,
            True,
            "quarantine_partial",
            "quarantine",
            PolicyLabel.NOMINAL_QUARANTINE_WEAK_PCT,
        ),
        # quarantine には reject のラベルを流用しない。P7 の
        # enforced_reject_domains に quarantine が混ざると指標が別物になる
        (
            "quarantine",
            None,
            None,
            True,
            "quarantine",
            "quarantine",
            PolicyLabel.ENFORCED_QUARANTINE,
        ),
        (
            "quarantine",
            None,
            None,
            False,
            "quarantine",
            "quarantine",
            PolicyLabel.BLIND_QUARANTINE,
        ),
        ("none", None, None, True, "none", "none", PolicyLabel.MONITORING),
        ("none", None, None, False, "none", "none", PolicyLabel.INEFFECTIVE),
        # pct=0 は実質 none
        ("reject", 0, None, True, "none", "reject", PolicyLabel.NOMINAL_REJECT_WEAK_PCT),
    ],
)
def test_policy_classification_table(p, pct, t, has_rua, eff_7489, eff_9989, label):
    got_7489, got_9989, got_label = dmarc_mod.classify_policy(p, pct, t, has_rua)
    assert got_7489 == eff_7489
    assert got_9989 == eff_9989
    assert got_label == label


def test_downgrade_one_step():
    assert dmarc_mod.downgrade_one_step("reject") == "quarantine"
    assert dmarc_mod.downgrade_one_step("quarantine") == "none"
    assert dmarc_mod.downgrade_one_step("none") == "none"


def test_multiple_dmarc_records_invalidates_the_policy():
    result = dmarc_mod.parse(["v=DMARC1; p=reject", "v=DMARC1; p=none"])
    assert result.multiple_records is True
    assert result.valid is False


def test_unknown_tags_are_kept_for_roundtrip():
    """未知タグは MUST スキップ（RFC 9989 §4.7）だが、生値は保持する。"""
    result = dmarc_mod.parse(["v=DMARC1; p=none; futuretag=abc"])
    assert result.unknown_tags == {"futuretag": "abc"}
    assert result.valid is True


def test_duplicate_tag_is_flagged_and_first_wins():
    result = dmarc_mod.parse(["v=DMARC1; p=reject; p=none"])
    assert result.has_duplicate_tag is True
    assert result.p == "reject"


def test_blind_enforcement_is_flagged():
    """強制しているが rua が無い。何が落ちているか運用者に見えない。"""
    result = dmarc_mod.parse(["v=DMARC1; p=reject"])
    assert result.blind_enforcement is True
    assert result.policy_label == PolicyLabel.BLIND_REJECT


def test_rua_local_part_is_not_stored():
    """個人情報を集めないという非目的（DESIGN.md 1.2）。"""
    result = dmarc_mod.parse(["v=DMARC1; p=none; rua=mailto:tanaka.taro@example.co.jp"])
    assert result.rua == ["example.co.jp"]
    assert "tanaka" not in str(result.rua)


def test_spec_version_detection():
    """pct があれば RFC 7489 世代、t=/np=/psd= があれば RFC 9989 世代。"""
    assert dmarc_mod.parse(["v=DMARC1; p=reject; pct=50"]).spec_version == SpecVersion.RFC7489
    assert dmarc_mod.parse(["v=DMARC1; p=reject; t=y"]).spec_version == SpecVersion.RFC9989
    assert dmarc_mod.parse(["v=DMARC1; p=reject; np=reject"]).spec_version == SpecVersion.RFC9989


def test_external_destination_verification_name():
    assert (
        dmarc_mod.authorization_record_name("example.jp", "reports.vendor.com")
        == "example.jp._report._dmarc.reports.vendor.com"
    )
    assert dmarc_mod.report_domain_is_external("example.jp", "example.jp") is False
    assert dmarc_mod.report_domain_is_external("example.jp", "vendor.com") is True


# ===========================================================================
# Organizational Domain の二重解決
# ===========================================================================


def test_tree_walk_names_go_upward():
    assert tree_walk_names("a.b.example.co.jp") == [
        "a.b.example.co.jp",
        "b.example.co.jp",
        "example.co.jp",
        "co.jp",
    ]


def test_tree_walk_caps_queries_for_deep_domains():
    """DoS 対策で1ドメインあたり最大8クエリ（RFC 9989）。"""
    names = tree_walk_names("a.b.c.d.e.f.g.h.i.example.jp")
    assert len(names) <= 8
    assert names[0] == "a.b.c.d.e.f.g.h.i.example.jp"  # Author Domain を最初に照会


def test_tree_walk_stops_only_on_psd():
    """psd を持たないレコードで停止してはいけない。

    停止させると Author Domain 自身が常に Organizational Domain になり、
    PSL とほぼ全件で食い違って差分の指標が意味を失う。
    """
    resolver = StaticResolver(
        {("_dmarc.send.example.jp", "TXT"): make_answer(
            "_dmarc.send.example.jp", "TXT", ["v=DMARC1; p=reject"]
        )}
    )
    result = resolve_org("send.example.jp", resolver)
    assert result.treewalk is None
    assert result.divergence is False  # 判定不能を差分として報告しない


def test_tree_walk_finds_org_domain_via_psd():
    resolver = StaticResolver(
        {("_dmarc.example.jp", "TXT"): make_answer(
            "_dmarc.example.jp", "TXT", ["v=DMARC1; p=reject; psd=n"]
        )}
    )
    result = resolve_org("send.example.jp", resolver)
    assert result.treewalk == "example.jp"
    assert result.psl == "example.jp"
    assert result.divergence is False
    assert result.treewalk_terminated_on_psd is True


def test_psl_private_section_produces_real_divergence():
    """PSL の PRIVATE セクションは Tree Walk と食い違う。これが検出したい差分。"""
    resolver = StaticResolver(
        {("_dmarc.s3.amazonaws.com", "TXT"): make_answer(
            "_dmarc.s3.amazonaws.com", "TXT", ["v=DMARC1; p=none; psd=n"]
        )}
    )
    result = resolve_org("x.s3.amazonaws.com", resolver)
    assert result.psl == "x.s3.amazonaws.com"
    assert result.treewalk == "s3.amazonaws.com"
    assert result.divergence is True


def test_psl_only_without_resolver():
    result = resolve_org("www.example.co.jp", None)
    assert result.psl == "example.co.jp"
    assert result.treewalk is None
    assert result.divergence is False


# ===========================================================================
# DKIM（三値表現）
# ===========================================================================


def test_dkim_not_found_is_not_the_same_as_absent():
    """「未設定」と「既知セレクタでは未検出」を厳密に区別する（原則5）。"""
    result = dkim_mod.build_result(found={}, selectors_tried=56, control_responded=False)
    assert result.status == DkimStatus.NOT_FOUND_IN_KNOWN_SELECTORS
    assert any("未設定の証明にはならない" in n for n in result.notes)


def test_dkim_not_applicable_when_no_selectors_tried():
    """階層C はセレクタを投げていない。未設定とは言えない。"""
    result = dkim_mod.build_result(
        found={}, selectors_tried=0, control_responded=False, applicable=False
    )
    assert result.status == DkimStatus.NOT_APPLICABLE


def test_dkim_detected():
    result = dkim_mod.build_result(
        found={"selector1": "v=DKIM1; k=rsa; p=" + "A" * 392},
        selectors_tried=56,
        control_responded=False,
    )
    assert result.status == DkimStatus.DETECTED
    assert result.selectors == ["selector1"]
    assert result.key_bits == [2048]


def test_dkim_empty_p_means_revoked():
    """p= が空なら失効（RFC 6376 §3.6.1）。"""
    key = dkim_mod.parse_key("s1", "v=DKIM1; p=")
    assert key.valid is True
    assert key.revoked is True


def test_dkim_wildcard_revoked_key_is_the_m3aawg_pattern():
    """*._domainkey に失効鍵。攻撃者がどのセレクタを騙っても失効鍵に当たる。"""
    result = dkim_mod.build_result(
        found={}, selectors_tried=0, control_responded=False,
        wildcard_record="v=DKIM1; p=", applicable=False,
    )
    assert result.wildcard_revoked_key is True


def test_dkim_control_response_flags_wildcard_dns():
    result = dkim_mod.build_result(
        found={"selector1": "v=DKIM1; p=AAA"}, selectors_tried=56, control_responded=True
    )
    assert result.wildcard_suspect is True
    assert any("信用できない" in n for n in result.notes)


def test_dkim_testing_flag():
    key = dkim_mod.parse_key("s1", "v=DKIM1; t=y; p=" + "A" * 392)
    assert key.testing is True


# ===========================================================================
# 周辺プロトコル
# ===========================================================================


def test_mta_sts_parsing():
    result = extras_mod.parse_mta_sts(["v=STSv1; id=20260801"])
    assert result.present is True
    assert result.id == "20260801"
    assert result.mode is None  # DNS だけでは分からない
    assert any("HTTPS" in n for n in result.notes)


def test_tls_rpt_parsing():
    result = extras_mod.parse_tls_rpt(["v=TLSRPTv1; rua=mailto:t@example.jp"])
    assert result.present is True
    assert result.rua == ["example.jp"]


def test_bimi_without_vmc_is_flagged():
    result = extras_mod.parse_bimi(["v=BIMI1; l=https://x/logo.svg"])
    assert result.present is True
    assert result.has_svg is True
    assert result.has_vmc is False
    assert any("VMC" in n for n in result.notes)


def test_dane_orphan_when_not_dnssec_signed():
    """TLSA はあるが親ゾーンが未署名で実効しない、という誤設定を検出する。"""
    result = extras_mod.parse_dane(["3 1 1 abcdef"], dnssec_signed=False)
    assert result.present is True
    assert result.orphan is True
    assert any("実効しない" in n for n in result.notes)


def test_dane_valid_when_signed():
    result = extras_mod.parse_dane(["3 1 1 abcdef"], dnssec_signed=True)
    assert result.orphan is False


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        # 階層は順序性を持つ。単純加算すると下位項目を二重評価する
        (dict(spf_present=True, dkim_detected=False, dmarc_present=False), 0),
        (dict(spf_present=True, dkim_detected=True, dmarc_present=True), 1),
        (dict(spf_present=True, dkim_detected=True, dmarc_present=True,
              dmarc_enforced=True), 2),
        (dict(spf_present=True, dkim_detected=True, dmarc_present=True,
              dmarc_enforced=True, tls_rpt_present=True, dnssec_signed=True), 3),
        (dict(spf_present=True, dkim_detected=True, dmarc_present=True,
              dmarc_enforced=True, tls_rpt_present=True, dnssec_signed=True,
              dane_present=True), 4),
    ],
)
def test_maturity_stage(kwargs, expected):
    defaults = dict(
        spf_present=False, dkim_detected=False, dmarc_present=False, dmarc_enforced=False,
        mta_sts_present=False, tls_rpt_present=False, dnssec_signed=False,
        bimi_with_vmc=False, dane_present=False,
    )
    assert extras_mod.maturity_stage(**{**defaults, **kwargs}) == expected


# ===========================================================================
# P5 の通し
# ===========================================================================


def _write_domains():
    write_parquet(
        [
            {
                "domain_id": "d:1", "entity_id": "jp:1", "run_id": RUN,
                "domain": "send.example.jp", "domain_role": "primary",
                "confidence": "confirmed", "is_measured": True,
                "measure_tier": MeasureTier.A, "evidence_count": 5,
            },
            {
                "domain_id": "d:2", "entity_id": "jp:1", "run_id": RUN,
                "domain": "parked.example.jp", "domain_role": "parked",
                "confidence": "parked", "is_measured": True,
                "measure_tier": MeasureTier.C, "evidence_count": 2,
            },
        ],
        phase_output(RUN, "p3_domains", "domains.parquet"),
        DOMAIN_ARROW_SCHEMA,
    )


ANSWERS = {
    ("send.example.jp", "MX"): make_answer(
        "send.example.jp", "MX", ["x.mail.protection.outlook.com."]
    ),
    ("send.example.jp", "TXT"): DnsAnswer(
        name="send.example.jp", rtype="TXT", observed=True, record_present=True,
        rcode="NOERROR",
        values=["v=spf1 include:spf.protection.outlook.com -all"],
        # 分割されたまま bronze に入る
        txt_strings=[["v=spf1 include:spf.protection.outlook.com ", "-all"]],
        authenticated_data=True,
    ),
    ("_dmarc.send.example.jp", "TXT"): make_answer(
        "_dmarc.send.example.jp", "TXT",
        ["v=DMARC1; p=reject; pct=10; rua=mailto:a@send.example.jp"],
    ),
    ("selector1._domainkey.send.example.jp", "TXT"): make_answer(
        "selector1._domainkey.send.example.jp", "TXT", ["v=DKIM1; k=rsa; p=" + "A" * 392]
    ),
    ("_mta-sts.send.example.jp", "TXT"): make_answer(
        "_mta-sts.send.example.jp", "TXT", ["v=STSv1; id=20260801"]
    ),
    ("parked.example.jp", "MX"): make_answer("parked.example.jp", "MX", ["."]),
    ("parked.example.jp", "TXT"): make_answer("parked.example.jp", "TXT", ["v=spf1 -all"]),
    ("_dmarc.parked.example.jp", "TXT"): make_answer(
        "_dmarc.parked.example.jp", "TXT", ["v=DMARC1; p=reject; sp=reject; adkim=s; aspf=s"]
    ),
    ("*._domainkey.parked.example.jp", "TXT"): make_answer(
        "*._domainkey.parked.example.jp", "TXT", ["v=DKIM1; p="]
    ),
}


class FakeBackend:
    name = "fake"
    version = "test"
    resolver_label = "fake"

    def __init__(self) -> None:
        self._r = StaticResolver(ANSWERS)
        self.stats = {"queries": 0, "cache_hits": 0, "tcp_failed": 0}

    def query(self, query):
        self.stats["queries"] += 1
        return self._r.query(query.name, query.rtype)


def facts() -> pd.DataFrame:
    from mailauth.io import read_parquet

    df = read_parquet(phase_output(RUN, "p5_parse", "facts.parquet"))
    assert df is not None
    return df.set_index("domain_id")


def test_p5_requires_bronze():
    with pytest.raises(MissingInputError, match="p4-measure"):
        run_p5(run_id=RUN)


def test_p5_parses_bronze_into_facts():
    _write_domains()
    run_p4(run_id=RUN, backend=FakeBackend())
    result = run_p5(run_id=RUN, resolver=StaticResolver(ANSWERS))

    assert result["status"] == "success"
    assert result["breakdown"]["domains_parsed"] == 2

    df = facts()
    send = df.loc["d:1"]
    # 分割された SPF が正しく連結されて解釈されている
    assert send["raw_spf"] == "v=spf1 include:spf.protection.outlook.com -all"
    assert send["spf_all_qualifier"] == "-"
    assert send["spf_lookup_count"] == 1
    # 二重計算。pct=10 で RFC 7489 と RFC 9989 が食い違う
    assert send["effective_7489"] == "quarantine"
    assert send["effective_9989"] == "reject"
    assert send["policy_label"] == PolicyLabel.NOMINAL_REJECT_WEAK_PCT
    assert send["dkim_status"] == DkimStatus.DETECTED
    assert list(send["dkim_key_bits"]) == [2048]
    assert bool(send["mta_sts_present"]) is True
    assert bool(send["dnssec_signed"]) is True


def test_p5_parked_domain_facts():
    _write_domains()
    run_p4(run_id=RUN, backend=FakeBackend())
    run_p5(run_id=RUN, resolver=StaticResolver(ANSWERS))

    parked = facts().loc["d:2"]
    assert bool(parked["mx_present"]) is False  # Null MX は MX 実在ではない
    assert parked["spf_all_qualifier"] == "-"
    assert parked["dmarc_p"] == "reject"
    # rua が無いので blind
    assert parked["policy_label"] == PolicyLabel.BLIND_REJECT
    # 階層C はセレクタを投げていないので not_applicable
    assert parked["dkim_status"] == DkimStatus.NOT_APPLICABLE


def test_p5_is_reproducible_from_bronze():
    """パーサにバグが見つかったら bronze から作り直せる（原則1の実質的な意味）。"""
    _write_domains()
    run_p4(run_id=RUN, backend=FakeBackend())
    run_p5(run_id=RUN, resolver=StaticResolver(ANSWERS))
    path = phase_output(RUN, "p5_parse", "facts.parquet")
    first = path.read_bytes()
    run_p5(run_id=RUN, resolver=StaticResolver(ANSWERS))
    assert path.read_bytes() == first


def test_p5_without_resolver_skips_tree_walk():
    _write_domains()
    run_p4(run_id=RUN, backend=FakeBackend())
    result = run_p5(run_id=RUN, resolver=None)
    assert "NO_RESOLVER" in {w["code"] for w in result["warnings"]}
    df = facts()
    assert df["org_domain_psl"].notna().all()  # PSL は resolver 不要
    assert df["org_domain_treewalk"].isna().all()


def test_p5_dry_run_writes_nothing():
    _write_domains()
    run_p4(run_id=RUN, backend=FakeBackend())
    result = run_p5(run_id=RUN, dry_run=True, resolver=None)
    assert result["outputs"] == []
    assert not phase_output(RUN, "p5_parse", "facts.parquet").exists()


def test_p5_spec_version_is_set_on_every_fact():
    """DESIGN.md 第9章「spec_version が全 fact に付与されている」。"""
    _write_domains()
    run_p4(run_id=RUN, backend=FakeBackend())
    run_p5(run_id=RUN, resolver=StaticResolver(ANSWERS))
    assert facts()["spec_version"].notna().all()


# ===========================================================================
# rua 宛先の「未登録」判定
#
# **「未登録」は強い主張である。** 「このドメインのレポートは第三者に
# 奪われうる」と公開することになるので、断定できるときだけ True にする。
# ===========================================================================


def _rua_case(rua_host: str, ns_answers: dict) -> dict:
    """rua が rua_host を指している fact を1件作る。"""
    from mailauth.p5_parse.runner import parse_domain

    domain = "example-co.jp"
    answers = {
        (domain, "TXT"): make_answer(domain, "TXT", ["v=spf1 -all"]),
        (f"_dmarc.{domain}", "TXT"): make_answer(
            f"_dmarc.{domain}", "TXT", [f"v=DMARC1; p=reject; rua=mailto:a@{rua_host}"]
        ),
        # 外部宛先の承認レコード（_report._dmarc）。あることにしておく
        (f"{domain}._report._dmarc.{_registrable(rua_host)}", "TXT"): make_answer(
            f"{domain}._report._dmarc.{_registrable(rua_host)}", "TXT", ["v=DMARC1"]
        ),
    }
    answers.update(ns_answers)
    by_purpose = {
        "dmarc": [
            {
                "domain": domain,
                "purpose": "dmarc",
                "query_name": f"_dmarc.{domain}",
                "observed": True,
                "record_present": True,
                # bronze は character-string の配列のまま持つ
                "answers": [
                    {"data": [f"v=DMARC1; p=reject; rua=mailto:a@{rua_host}"]}
                ],
            }
        ]
    }
    return parse_domain(domain, by_purpose, resolver=StaticResolver(answers))


def _registrable(host: str) -> str:
    from mailauth.normalize import etld_plus_one

    return etld_plus_one(host) or host


def test_a_subdomain_rua_target_is_not_called_unregistered():
    """**rua の宛先はサブドメインが普通。** NS が無いのはゾーンを切って
    いないだけで、未登録ではない。

    実測（2026-08）で rx.rakuten.co.jp と ml.tepco.co.jp が「未登録」と
    判定されていた。楽天や東電のレポートが第三者に奪われうる、という
    事実に反する主張を公開しかけていた。
    """
    out = _rua_case(
        "rx.other-example.jp",
        {
            # サブドメインは NODATA（名前は在るが NS が無い）
            ("rx.other-example.jp", "NS"): make_answer(
                "rx.other-example.jp", "NS", [], rcode="NODATA"
            ),
            # 登録可能ドメインには NS がある
            ("other-example.jp", "NS"): make_answer(
                "other-example.jp", "NS", ["ns1.other-example.jp."]
            ),
        },
    )
    assert out["rua_external"] is True
    assert out["rua_domain_unregistered"] is False


def test_a_genuinely_nonexistent_rua_domain_is_flagged():
    """名前自体が存在しない（NXDOMAIN）なら第三者が登録できる。

    Hureau et al.（PAM 2024）が指摘した実害のある構成。
    """
    out = _rua_case(
        "rua.gone-example.jp",
        {
            ("gone-example.jp", "NS"): make_answer(
                "gone-example.jp", "NS", [], rcode="NXDOMAIN"
            ),
        },
    )
    assert out["rua_domain_unregistered"] is True


def test_an_unobservable_rua_domain_is_not_called_unregistered():
    """取れなかったものを「未登録」と言わない（原則5）。**False でもない。**"""
    out = _rua_case(
        "rua.unknown-example.jp",
        {
            ("unknown-example.jp", "NS"): make_answer(
                "unknown-example.jp", "NS", [], observed=False, rcode="SERVFAIL"
            ),
        },
    )
    assert out["rua_domain_unregistered"] is None


def test_the_registrable_domain_is_queried_not_the_rua_host():
    """登録の有無は eTLD+1 で見る。ホスト名で見ると誤判定になる。"""
    import inspect

    from mailauth.p5_parse import runner as p5

    source = inspect.getsource(p5.parse_domain)
    assert "etld_plus_one(ext)" in source
    assert 'ns.rcode == "NXDOMAIN"' in source
