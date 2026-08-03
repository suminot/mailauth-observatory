"""P6 推察。

検証の主眼は3つ。
  1. 二段推定が単一ベンダーに丸めないこと（原則2）
  2. パーク分類が「取れなかった」を「放置」と呼ばないこと（原則5）
  3. 推定に必ず evidence が付くこと。根拠を辿れない推定を出さないこと
"""

from __future__ import annotations

import json

import pytest

from mailauth.contracts import (
    FACT_ARROW_SCHEMA,
    ConfidenceLevel,
    InferenceCategory,
    ParkClass,
)
from mailauth.io import read_parquet, write_parquet
from mailauth.p6_infer import MissingInputError
from mailauth.p6_infer import park as park_mod
from mailauth.p6_infer import run as run_p6
from mailauth.p6_infer.fingerprints import (
    FingerprintError,
    RuleSet,
    load_all,
    load_file,
)
from mailauth.p6_infer.match import (
    NOT_DETECTED_VENDOR,
    STALE_CONSECUTIVE_MONTHS,
    UNDETECTABLE_API_MODE,
    build_drafts,
    combine_confidence,
    find_hits,
    is_corroborated,
)
from mailauth.p6_infer.runner import unknown_mx_hosts, vendor_share
from mailauth.paths import phase_output

RUN = "2026-08"
PREV = "2026-07"


@pytest.fixture(scope="module")
def rules() -> RuleSet:
    return load_all()


def _fact(**kwargs) -> dict:
    """既定値は「何も無い」ではなく「観測できた上で何も無かった」。"""
    base = {
        "fact_id": "f:1",
        "domain_id": "d:1",
        "entity_id": "jp:1",
        "run_id": RUN,
        "measured_month": __import__("datetime").date(2026, 8, 1),
        "observed": True,
        "record_present": True,
        "mx_hosts": [],
        "mx_present": False,
        "mx_null": False,
        "spf_present": False,
        "spf_includes": [],
        "spf_mechanisms": [],
        "spf_all_qualifier": None,
        "dkim_cname_targets": [],
        "dkim_wildcard_revoked": False,
        "verification_txt": [],
        "dmarc_present": False,
        "dmarc_rua": [],
        "effective_7489": None,
    }
    base.update(kwargs)
    return base


# ===========================================================================
# 辞書の読み込み
# ===========================================================================


def test_all_shipped_fingerprints_load(rules):
    """同梱の辞書がすべて検証を通ること。壊れた規則は永久に一致しない。"""
    assert len(rules.rules) > 30
    assert rules.undetectable, "API 連携型製品の一覧が読めていない"
    # 版はファイル名を含む決定的な文字列。inference に記録して再現性を担保する
    assert "platforms.yaml=" in rules.version


def test_broken_pattern_raises(tmp_path):
    """コンパイルできない正規表現は黙って無視しない。

    無視すると「一致0件」と「規則が壊れている」を区別できなくなる。
    """
    path = tmp_path / "broken.yaml"
    path.write_text(
        "version: t\ncategory: esp\nrules:\n"
        "  - id: x\n    vendor: V\n    match:\n"
        "      record: MX\n      pattern: '('\n",
        encoding="utf-8",
    )
    with pytest.raises(FingerprintError, match="コンパイルできない"):
        load_file(path)


def test_unknown_record_type_raises(tmp_path):
    """record の誤記も検出する。誤記した規則は一致しようがない。"""
    path = tmp_path / "typo.yaml"
    path.write_text(
        "version: t\ncategory: esp\nrules:\n"
        "  - id: x\n    vendor: V\n    match:\n"
        "      record: SPF_INCLUDES\n      pattern: 'x'\n",
        encoding="utf-8",
    )
    with pytest.raises(FingerprintError, match="record が未知"):
        load_file(path)


def test_duplicate_rule_id_raises(tmp_path, monkeypatch):
    """id が重複すると evidence の rule_id が指す先が曖昧になる。"""
    d = tmp_path / "fp"
    d.mkdir()
    body = (
        "version: t\ncategory: esp\nrules:\n"
        "  - id: same\n    vendor: V\n    match:\n"
        "      record: MX\n      pattern: 'a'\n"
    )
    (d / "a.yaml").write_text(body, encoding="utf-8")
    (d / "b.yaml").write_text(body, encoding="utf-8")
    with pytest.raises(FingerprintError, match="重複"):
        load_all(d, rua_vendors=None)


# ===========================================================================
# 二段推定
# ===========================================================================


def test_gateway_and_platform_are_kept_separate(rules):
    """MX がゲートウェイでも背後の実基盤を別カテゴリで残す（DESIGN.md P6）。

    IIJ セキュアMX が前段、実基盤は M365。単一ベンダーに丸めると
    「M365 の上に IIJ」という実態が消える。
    """
    drafts = build_drafts(
        _fact(
            mx_hosts=["mx1.cust.securemx.jp"],
            mx_present=True,
            spf_present=True,
            spf_includes=["spf.protection.outlook.com"],
        ),
        rules,
    )
    by_category = {d.category: d for d in drafts}
    assert by_category[InferenceCategory.SECURITY_GATEWAY].vendor == "IIJ"
    assert by_category[InferenceCategory.MAIL_PLATFORM].vendor == "Microsoft"


def test_three_point_agreement_reaches_high(rules):
    """MX=Cisco + TXT MS= + DKIM →onmicrosoft は高（DESIGN.md P6 の表）。"""
    drafts = build_drafts(
        _fact(
            mx_hosts=["a.iphmx.com"],
            mx_present=True,
            spf_present=True,
            spf_includes=["spf.protection.outlook.com"],
            verification_txt=["MS=ms12345678"],
            dkim_cname_targets=["sel._domainkey.x.onmicrosoft.com"],
        ),
        rules,
    )
    platform = next(d for d in drafts if d.category == InferenceCategory.MAIL_PLATFORM)
    assert platform.vendor == "Microsoft"
    assert platform.confidence == ConfidenceLevel.HIGH
    gateway = next(d for d in drafts if d.category == InferenceCategory.SECURITY_GATEWAY)
    assert gateway.vendor == "Cisco"


def test_gateway_signing_domain_is_not_read_as_platform(rules):
    """IIJ の署名ドメインを実基盤と誤認しない（DESIGN.md P6 実装メモ）。

    dxg.dox.jp はアライメント不可のゲートウェイ独自ドメイン。
    これを mail_platform と読むと「IIJ がメール基盤」という誤りになる。
    """
    drafts = build_drafts(
        _fact(dkim_cname_targets=["sel._domainkey.dxg.dox.jp"]), rules
    )
    assert [d.category for d in drafts] == [InferenceCategory.SECURITY_GATEWAY]
    assert drafts[0].vendor == "IIJ"


def test_spf_mechanism_catches_sakura(rules):
    """さくらは専用 include を持たない。include だけ見ると取りこぼす。"""
    drafts = build_drafts(
        _fact(spf_present=True, spf_mechanisms=["a:www1234.sakura.ne.jp", "mx"]), rules
    )
    assert [d.vendor for d in drafts] == ["さくらインターネット"]


def test_numbered_include_is_wildcarded(rules):
    """番号付き include は番号部分をワイルドカード化する（DESIGN.md P6 実装メモ）。"""
    for include in ("spf.gmoserver.jp", "spf12.gmoserver.jp"):
        drafts = build_drafts(_fact(spf_includes=[include]), rules)
        assert drafts, f"{include} が一致していない"


def test_dkim_cname_upgrades_confidence(rules):
    """DKIM CNAME は署名基盤。最も実基盤に近いので high に上げる。"""
    weak = build_drafts(_fact(spf_includes=["spf12.biglobe.ne.jp"]), rules)[0]
    assert weak.confidence == ConfidenceLevel.MEDIUM

    strong = build_drafts(
        _fact(dkim_cname_targets=["x.dkim.amazonses.com"]), rules
    )[0]
    assert strong.confidence == ConfidenceLevel.HIGH


def test_two_independent_evidence_types_upgrade_one_level():
    """独立した証拠が2種類そろえば1段上げる。"""
    import re

    from mailauth.contracts import EvidenceRecordType
    from mailauth.p6_infer.fingerprints import Rule

    def rule(rid, record):
        return Rule(
            id=rid,
            vendor="V",
            category=InferenceCategory.MAIL_PLATFORM,
            record=record,
            pattern=re.compile("x"),
            confidence=ConfidenceLevel.LOW,
        )

    from mailauth.p6_infer.match import Hit

    one = [Hit(rule("a", EvidenceRecordType.MX), EvidenceRecordType.MX, "x")]
    assert combine_confidence(one)[0] == ConfidenceLevel.LOW

    two = [
        *one,
        Hit(
            rule("b", EvidenceRecordType.SPF_INCLUDE),
            EvidenceRecordType.SPF_INCLUDE,
            "x",
        ),
    ]
    assert combine_confidence(two)[0] == ConfidenceLevel.MEDIUM


def test_evidence_is_always_recorded(rules):
    """根拠を辿れない推定は出さない（原則2）。"""
    drafts = build_drafts(_fact(mx_hosts=["a.ppe-hosted.com"], mx_present=True), rules)
    evidence = json.loads(drafts[0].evidence_json())
    assert evidence == [
        {
            "record_type": "MX",
            "matched_value": "a.ppe-hosted.com",
            "rule_id": "pp-mx-02",
        }
    ]


def test_all_matches_are_kept_not_just_the_first(rules):
    """最初の一致で打ち切らない。複数ベンダーが立つのは正常な状態。"""
    hits = find_hits(
        _fact(
            mx_hosts=["a.mimecast.com"],
            spf_includes=["spf.protection.outlook.com", "sendgrid.net"],
            dmarc_rua=["dmarc25.jp"],
        ),
        rules,
    )
    vendors = {h.rule.vendor for h in hits}
    assert {"Mimecast", "Microsoft", "Twilio", "TwoFive"} <= vendors


# ===========================================================================
# 所有権確認TXT の stale 判定
# ===========================================================================


def test_verification_txt_alone_is_low_confidence(rules):
    drafts = build_drafts(_fact(verification_txt=["MS=ms12345678"]), rules)
    assert drafts[0].confidence == ConfidenceLevel.LOW
    assert "所有権確認 TXT のみ" in " / ".join(drafts[0].notes)


def test_corroborated_verification_is_not_stale(rules):
    drafts = build_drafts(
        _fact(
            verification_txt=["MS=ms12345678"],
            mx_hosts=["x.mail.protection.outlook.com"],
            mx_present=True,
        ),
        rules,
    )
    platform = next(d for d in drafts if d.vendor == "Microsoft")
    assert platform.is_stale is False
    assert platform.stale_streak_months is None


def test_uncorroborated_verification_needs_three_months(rules):
    """1か月目では降格しない。月次差分での観測が最も確実な判別手段。"""
    fact = _fact(verification_txt=["MS=ms12345678"])

    first = build_drafts(fact, rules)[0]
    assert first.is_stale is False
    assert first.stale_streak_months == 1

    key = (InferenceCategory.MAIL_PLATFORM, "Microsoft")
    third = build_drafts(
        fact, rules, prior_streaks={key: STALE_CONSECUTIVE_MONTHS - 1}
    )[0]
    assert third.stale_streak_months == STALE_CONSECUTIVE_MONTHS
    assert third.is_stale is True


def test_corroboration_without_rules_is_none(rules):
    """裏付け条件が辞書に無いものは「判定していない」。無いとは言わない。"""
    atlassian = next(r for r in rules.rules if r.id == "atlassian-verify-01")
    assert is_corroborated(atlassian, _fact()) is None

    ms = next(r for r in rules.rules if r.id == "ms-verify-01")
    assert is_corroborated(ms, _fact()) is False


# ===========================================================================
# パークドメイン分類
# ===========================================================================


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        # 模範的。M3AAWG 推奨構成
        (
            dict(mx_null=True, spf_present=True, spf_all_qualifier="-",
                 dmarc_present=True, effective_7489="reject"),
            ParkClass.HARDENED_PARKED,
        ),
        # Null MX が無くても quarantine 以上なら十分に固められている
        (
            dict(spf_present=True, spf_all_qualifier="~", dmarc_present=True,
                 effective_7489="quarantine"),
            ParkClass.DEFENDED_PARKED,
        ),
        # 送信禁止の意図はあるが DMARC が弱い
        (
            dict(spf_present=True, spf_all_qualifier="-", dmarc_present=True,
                 effective_7489="none"),
            ParkClass.INTENTIONAL_NO_SEND,
        ),
        # 放置。なりすましの出発点になりうる
        (dict(), ParkClass.NEGLECTED),
        # 通常の送信ドメイン。パーク分類の対象外
        (
            dict(mx_hosts=["a.example"], mx_present=True, spf_present=True),
            ParkClass.ACTIVE_SENDING,
        ),
        # MX はあるが SPF が無い。矛盾
        (
            dict(mx_hosts=["a.example"], mx_present=True),
            ParkClass.INCONSISTENT,
        ),
        # SPF はあるが all が無く DMARC も無い。矛盾
        (
            dict(spf_present=True, spf_all_qualifier=None),
            ParkClass.INCONSISTENT,
        ),
    ],
)
def test_park_classification(kwargs, expected):
    assert park_mod.classify(_fact(**kwargs)).park_class == expected


def test_unobserved_domain_is_not_neglected():
    """SERVFAIL のドメインを「放置」と呼ぶのは事実の捏造である（原則5）。"""
    result = park_mod.classify(_fact(observed=False, record_present=None))
    assert result.park_class is None
    assert "取れなかった" in " / ".join(result.notes)


def test_hardened_requires_reject_not_quarantine():
    """hardened は p=reject のみ。quarantine は defended に留める。"""
    result = park_mod.classify(
        _fact(mx_null=True, spf_present=True, spf_all_qualifier="-",
              dmarc_present=True, effective_7489="quarantine")
    )
    assert result.park_class == ParkClass.DEFENDED_PARKED


def test_park_denominators():
    assert park_mod.is_parked(ParkClass.NEGLECTED) is True
    # 通常の送信ドメインと矛盾は分母から外す
    assert park_mod.is_parked(ParkClass.ACTIVE_SENDING) is False
    assert park_mod.is_parked(ParkClass.INCONSISTENT) is False
    assert park_mod.is_defended(ParkClass.HARDENED_PARKED) is True
    assert park_mod.is_defended(ParkClass.INTENTIONAL_NO_SEND) is False


def test_wildcard_revoked_key_is_noted():
    result = park_mod.classify(
        _fact(mx_null=True, spf_present=True, spf_all_qualifier="-",
              dmarc_present=True, effective_7489="reject",
              dkim_wildcard_revoked=True)
    )
    assert result.has_wildcard_dkim_revoked is True
    # 実効的な防御力は小さいことを併記する
    assert "実効的な防御力は小さい" in " / ".join(result.notes)


# ===========================================================================
# 未知 MX ホスト（辞書を育てる装置）
# ===========================================================================


def test_unknown_mx_hosts_are_aggregated_by_registered_domain():
    """顧客別ホスト名を1件ずつ数えても辞書を育てる手がかりにならない。"""
    facts = [
        _fact(mx_hosts=[f"mx{i}.cust{i}.unknown-vendor.co.jp"]) for i in range(5)
    ]
    facts.append(_fact(mx_hosts=["a.mail.protection.outlook.com"]))
    ranked = unknown_mx_hosts(facts, {"a.mail.protection.outlook.com"})

    assert ranked[0]["registered_domain"] == "unknown-vendor.co.jp"
    assert ranked[0]["count"] == 5
    assert len(ranked[0]["examples"]) == 3
    # 辞書に一致した M365 は未知に数えない
    assert all(r["registered_domain"] != "outlook.com" for r in ranked)


# ===========================================================================
# P6 の通し
# ===========================================================================


def _write_facts(rows: list[dict], run_id: str = RUN) -> None:
    write_parquet(rows, phase_output(run_id, "p5_parse", "facts.parquet"),
                  FACT_ARROW_SCHEMA)


def _inferences(run_id: str = RUN):
    frame = read_parquet(phase_output(run_id, "p6_infer", "inferences.parquet"))
    assert frame is not None
    return frame


def test_p6_requires_facts():
    with pytest.raises(MissingInputError, match="p5-parse"):
        run_p6(run_id=RUN)


def test_p6_end_to_end():
    _write_facts(
        [
            _fact(
                domain_id="d:1",
                mx_hosts=["mx1.cust.securemx.jp"],
                mx_present=True,
                spf_present=True,
                spf_includes=["spf.protection.outlook.com"],
                spf_all_qualifier="-",
                dmarc_present=True,
                effective_7489="reject",
                dmarc_rua=["dmarc25.jp"],
            ),
            _fact(
                domain_id="d:2",
                fact_id="f:2",
                mx_null=True,
                spf_present=True,
                spf_all_qualifier="-",
                dmarc_present=True,
                effective_7489="reject",
            ),
        ]
    )
    result = run_p6(run_id=RUN)
    assert result["status"] == "success"

    frame = _inferences()
    d1 = frame[frame["domain_id"] == "d:1"]
    assert set(d1["category"]) == {
        InferenceCategory.SECURITY_GATEWAY,
        InferenceCategory.MAIL_PLATFORM,
        InferenceCategory.DMARC_VENDOR,
    }
    # パーク分類はドメイン単位。二重に数えないよう1行だけに載る
    assert d1["park_class"].notna().sum() == 1
    assert set(d1["park_class"].dropna()) == {ParkClass.ACTIVE_SENDING}

    d2 = frame[frame["domain_id"] == "d:2"]
    assert set(d2["park_class"].dropna()) == {ParkClass.HARDENED_PARKED}


def test_p6_marks_undetectable_when_no_gateway_found():
    """「検出されなかった＝使っていない」ではない（DESIGN.md P6）。"""
    _write_facts([_fact(mx_hosts=["a.mail.protection.outlook.com"], mx_present=True)])
    run_p6(run_id=RUN)

    frame = _inferences()
    sentinel = frame[frame["vendor"] == NOT_DETECTED_VENDOR]
    assert len(sentinel) == 1
    assert sentinel.iloc[0]["undetectable_reason"] == UNDETECTABLE_API_MODE
    assert sentinel.iloc[0]["category"] == InferenceCategory.SECURITY_GATEWAY
    assert "使っていない" in sentinel.iloc[0]["note"]

    # シェアの分母には混ぜない
    share = vendor_share(frame, InferenceCategory.SECURITY_GATEWAY)
    assert share == []


def test_p6_reports_unknown_mx_hosts_in_manifest():
    """未知 MX ホストが manifest に出ること（受け入れ基準）。"""
    _write_facts(
        [_fact(domain_id=f"d:{i}", mx_hosts=["mx.unknown-vendor.example"],
               mx_present=True) for i in range(3)]
    )
    result = run_p6(run_id=RUN)

    unknown = result["breakdown"]["unknown_mx_hosts"]
    assert unknown[0]["registered_domain"] == "unknown-vendor.example"
    assert unknown[0]["count"] == 3
    assert any(w["code"] == "UNKNOWN_MX_HOSTS" for w in result["warnings"])
    # 推定が1件も付かないので受け入れ基準の警告も出る
    assert result["breakdown"]["domains_with_no_inference"] == 3
    assert any(w["code"] == "NO_INFERENCE_RATE_HIGH" for w in result["warnings"])


def test_p6_warns_when_park_cannot_be_classified():
    _write_facts([_fact(observed=False, record_present=None)])
    result = run_p6(run_id=RUN)
    assert result["breakdown"]["park_unclassified"] == 1
    assert any(w["code"] == "PARK_UNCLASSIFIED" for w in result["warnings"])


def test_p6_carries_stale_streak_across_months():
    """前月の連続月数を引き継いで降格する。"""
    fact = _fact(verification_txt=["MS=ms12345678"])
    _write_facts([fact], run_id=PREV)
    run_p6(run_id=PREV)
    assert int(_inferences(PREV).iloc[0]["stale_streak_months"]) == 1

    _write_facts([fact], run_id=RUN)
    result = run_p6(run_id=RUN)
    row = _inferences()[_inferences()["vendor"] == "Microsoft"].iloc[0]
    assert int(row["stale_streak_months"]) == 2
    assert bool(row["is_stale"]) is False
    assert result["breakdown"]["stale_pending"] == 1
    # 履歴があるので初回警告は出ない
    assert not any(w["code"] == "NO_STALE_HISTORY" for w in result["warnings"])


def test_p6_without_history_warns():
    _write_facts([_fact()])
    result = run_p6(run_id=RUN)
    assert any(w["code"] == "NO_STALE_HISTORY" for w in result["warnings"])


def test_p6_is_idempotent():
    """辞書を更新したら作り直せる。同じ入力なら同じバイト列（原則6）。"""
    _write_facts([_fact(mx_hosts=["a.pphosted.com"], mx_present=True)])
    run_p6(run_id=RUN)
    path = phase_output(RUN, "p6_infer", "inferences.parquet")
    first = path.read_bytes()
    run_p6(run_id=RUN)
    assert path.read_bytes() == first


def test_p6_records_fingerprint_version():
    """どの辞書で推定したかを残す。後から再現できないと検証できない。"""
    _write_facts([_fact(mx_hosts=["a.iphmx.com"], mx_present=True)])
    result = run_p6(run_id=RUN)
    assert "platforms.yaml=" in result["tool_versions"]["fingerprints"]
    assert "platforms.yaml=" in _inferences().iloc[0]["fingerprint_version"]


# ===========================================================================
# 実データで見つかった取りこぼし（回帰防止）
# ===========================================================================


def test_real_proofpoint_hostname_matches(rules):
    """実測形は `mxa-00512b01.gslb.pphosted.com`。

    DESIGN.md の擬似コードの `^mx0[ab]-[0-9a-f]{8}\\.pphosted\\.com$` は
    `mx0a-` 固定・`.gslb` 無しを前提にしており、実データを取りこぼしていた。
    """
    drafts = build_drafts(
        _fact(
            mx_hosts=[
                "mxa-00512b01.gslb.pphosted.com",
                "mxb-00512b01.gslb.pphosted.com",
            ],
            mx_present=True,
        ),
        rules,
    )
    gateway = next(d for d in drafts if d.category == InferenceCategory.SECURITY_GATEWAY)
    assert gateway.vendor == "Proofpoint"
    assert "pp-mx-01" in gateway.rule_ids


def test_specific_rule_wins_product_over_catch_all(rules):
    """受け皿の広い規則が詳細な規則の product を上書きしないこと。

    `\\.pphosted\\.com$` の受け皿（pp-mx-03）を足せるようにしておきたいが、
    それが系列の区別を消してしまうと product が粗くなる。
    """
    drafts = build_drafts(
        _fact(mx_hosts=["mxa-00512b01.gslb.pphosted.com"], mx_present=True), rules
    )
    gateway = next(d for d in drafts if d.vendor == "Proofpoint")
    # 両方当たっている状態で、詳細な側の product が残る
    assert set(gateway.rule_ids) == {"pp-mx-01", "pp-mx-03"}
    assert gateway.product == "Proofpoint Enterprise Protection"


def test_unmatched_pphosted_host_still_infers_proofpoint(rules):
    """命名が変わっても未知ホストに落とさない。系列だけ不明にする。"""
    drafts = build_drafts(
        _fact(mx_hosts=["mx-newstyle.pphosted.com"], mx_present=True), rules
    )
    gateway = next(d for d in drafts if d.vendor == "Proofpoint")
    assert gateway.rule_ids == ["pp-mx-03"]
    assert gateway.product is None


def test_saas_verification_txt_is_not_called_a_mail_platform(rules):
    """Docusign の所有権確認 TXT を「メール基盤は Docusign」と読むのは誤り。

    辞書からカテゴリが決まらないベンダーは esp（配信基盤）に寄せ、
    推測であることを note に残す。
    """
    drafts = build_drafts(
        _fact(verification_txt=["docusign=aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"]), rules
    )
    assert [d.category for d in drafts] == [InferenceCategory.ESP]
    assert "辞書から決められない" in " / ".join(drafts[0].notes)


def test_verification_txt_of_a_known_platform_keeps_its_category(rules):
    """Microsoft は他の規則からカテゴリが決まるので推測しない。"""
    drafts = build_drafts(_fact(verification_txt=["MS=ms12345678"]), rules)
    assert [d.category for d in drafts] == [InferenceCategory.MAIL_PLATFORM]
    assert "辞書から決められない" not in " / ".join(drafts[0].notes)
