"""P9 事前通知の準備（DESIGN.md Sprint 9）。

**このモジュールが送らないことを、テストでも確かめる。** 送信の可否は運用上の
判断なので、コードに送信経路が生えたら落ちるようにしておく。

守らせたいのは4つの門である。どれも「警告して続行」ではなく停止であること。
"""

from __future__ import annotations

import datetime as dt

import pytest

from mailauth.p8_publish import vocabulary
from mailauth.p9_notify import contacts, optout, plan, selfcheck, template
from mailauth.resolver import StaticResolver, make_answer

# --------------------------------------------------------------------------
# 助け
# --------------------------------------------------------------------------

URLS = {
    "detail_url": "https://obs.example.org/companies/{domain}",
    "method_url": "https://obs.example.org/method",
}
SENDER = "obs.example.org"


def _compliant(domain: str = SENDER) -> selfcheck.SelfComplianceResult:
    result = selfcheck.SelfComplianceResult(domain=domain)
    result.checks = {name: True for name in selfcheck.REQUIREMENTS}
    return result


def _registry(tmp_path, rows: str = "") -> optout.OptOutRegistry:
    path = tmp_path / "optout.csv"
    path.write_text("domain,requested_on,scope,note\n" + rows, encoding="utf-8")
    return optout.load(path)


def _contacts(domain: str, *, source: str = "rfc2142") -> contacts.ContactSet:
    cs = contacts.ContactSet(domain=domain)
    cs.candidates.append(
        contacts.ContactCandidate(
            address=f"security@{domain}",
            source=source,
            confidence=contacts.SOURCE_CONFIDENCE[source],
        )
    )
    return cs


#: 指摘に当たる事実がある観測。p=none / SPF ?all / DKIM 未検出
FACT = {
    "observed": True,
    "spf_present": True,
    "spf_valid": True,
    "spf_all_qualifier": "?",
    "dmarc_present": True,
    "effective_7489": "none",
    "dmarc_rua": [],
    "dkim_status": "not_found",
}


def _target(domain: str = "target-example.jp", **overrides) -> plan.NotifyTarget:
    fact = {**FACT, **overrides.pop("fact", {})}
    return plan.NotifyTarget(
        domain=domain,
        fact=fact,
        contacts=overrides.pop("contacts", None) or _contacts(domain),
        entity_name=overrides.pop("entity_name", "株式会社テスト"),
        entity_id=overrides.pop("entity_id", "E1"),
    )


def _plan(tmp_path, targets, **overrides):
    kwargs = dict(
        sender_domain=SENDER,
        self_check=_compliant(),
        registry=_registry(tmp_path),
        correction_contact="corrections@obs.example.org",
        measured_month="2026-08",
        notified_on=dt.date(2026, 8, 4),
        **URLS,
    )
    kwargs.update(overrides)
    return plan.build_plan(targets, **kwargs)


# --------------------------------------------------------------------------
# 門1: 送信元自身の準拠
# --------------------------------------------------------------------------


def test_a_non_compliant_sender_cannot_build_a_plan(tmp_path):
    """**自分の設定が不備な状態で他社に通知しない。** 警告では済ませない。"""
    bad = _compliant()
    bad.checks["dmarc_enforced"] = False
    with pytest.raises(selfcheck.SelfComplianceError) as exc:
        _plan(tmp_path, [_target()], self_check=bad)
    assert "dmarc_enforced" in str(exc.value)


def test_every_requirement_is_load_bearing(tmp_path):
    """要件のうち1つでも欠ければ止まる。**どれも譲らない。**"""
    for name in selfcheck.REQUIREMENTS:
        bad = _compliant()
        bad.checks[name] = False
        with pytest.raises(selfcheck.SelfComplianceError):
            _plan(tmp_path, [_target()], self_check=bad)


def test_an_unobserved_sender_is_not_treated_as_compliant():
    """取れなかった場合を「準拠」にしない（原則5）。"""
    resolver = StaticResolver(
        {
            ("obs.example.org", "TXT"): make_answer(
                "obs.example.org", "TXT", observed=False, rcode="SERVFAIL"
            )
        }
    )
    result = selfcheck.check_self(SENDER, resolver=resolver)
    assert result.compliant is False
    assert all(result.checks[name] is None for name in selfcheck.REQUIREMENTS)
    assert any("取れなかった" in n for n in result.notes)


def test_the_self_check_measures_the_sender_like_any_other_domain():
    resolver = StaticResolver(
        {
            ("obs.example.org", "TXT"): make_answer(
                "obs.example.org", "TXT", ["v=spf1 include:_spf.example.net -all"]
            ),
            ("_dmarc.obs.example.org", "TXT"): make_answer(
                "_dmarc.obs.example.org",
                "TXT",
                ["v=DMARC1; p=reject; rua=mailto:rua@obs.example.org"],
            ),
            ("sel1._domainkey.obs.example.org", "TXT"): make_answer(
                "sel1._domainkey.obs.example.org",
                "TXT",
                ["v=DKIM1; k=rsa; p=MIIBIjANBg"],
            ),
        }
    )
    result = selfcheck.check_self(SENDER, resolver=resolver, selectors=["sel1"])
    assert result.compliant is True
    assert result.failed == []


def test_missing_dkim_selectors_are_not_silently_passed():
    """セレクタを渡さなければ準拠にはならない。**「未検出」を「無い」にしない。**"""
    resolver = StaticResolver(
        {
            ("obs.example.org", "TXT"): make_answer(
                "obs.example.org", "TXT", ["v=spf1 -all"]
            ),
            ("_dmarc.obs.example.org", "TXT"): make_answer(
                "_dmarc.obs.example.org",
                "TXT",
                ["v=DMARC1; p=reject; rua=mailto:r@obs.example.org"],
            ),
        }
    )
    result = selfcheck.check_self(SENDER, resolver=resolver)
    assert "dkim_detected" in result.failed
    assert any("自分のセレクタは自分が知っている" in n for n in result.notes)


# --------------------------------------------------------------------------
# 門2: オプトアウト登録簿
# --------------------------------------------------------------------------


def test_an_unreadable_registry_is_not_an_empty_registry(tmp_path):
    """**「読めなかった」を「誰も断っていない」にしない。** ここは実害が出る。"""
    registry = optout.load(tmp_path / "nope.csv")
    assert registry.available is False
    assert registry.entries == []
    with pytest.raises(plan.PlanBlockedError) as exc:
        _plan(tmp_path, [_target()], registry=registry)
    assert "誰も断っていない" in str(exc.value)


def test_an_empty_registry_is_distinguishable_from_a_missing_one(tmp_path):
    registry = _registry(tmp_path)
    assert registry.available is True
    assert registry.entries == []
    assert _plan(tmp_path, [_target()], registry=registry).count == 1


def test_opt_out_covers_subdomains(tmp_path):
    """`example.jp` が断ったら `mail.example.jp` にも送らない。"""
    registry = _registry(tmp_path, "example.jp,2026-07-01,domain,\n")
    assert registry.contains("mail.example.jp") is True
    assert registry.contains("notexample.jp") is False
    result = _plan(tmp_path, [_target("mail.example.jp")], registry=registry)
    assert result.count == 0
    assert result.skipped[0].reason == "オプトアウト"


def test_entity_scoped_opt_out_covers_the_other_domains_of_the_company(tmp_path):
    registry = _registry(tmp_path, "a-example.jp,2026-07-01,entity,\n")
    targets = [_target("a-example.jp"), _target("b-example.jp")]
    result = _plan(tmp_path, targets, registry=registry)
    assert result.count == 0
    assert {s.domain for s in result.skipped} == {"a-example.jp", "b-example.jp"}


def test_the_registry_has_no_expiry_column():
    """**期限の列を作らない。** 断られた相手は恒久的に対象外にする。"""
    assert "expires" not in optout.COLUMNS
    assert "expires_on" not in optout.COLUMNS
    assert not hasattr(optout.OptOutEntry, "expires")


def test_the_registry_has_no_delete_function():
    """取り消しは人が git 上で行う。コードに削除経路を持たせない。"""
    assert not hasattr(optout, "remove")
    assert not hasattr(optout, "delete")
    assert not hasattr(optout.OptOutRegistry, "remove")


def test_append_creates_the_file_with_a_header(tmp_path):
    path = tmp_path / "sub" / "optout.csv"
    optout.append("x-example.jp", note="返信で辞退", path=path)
    text = path.read_text(encoding="utf-8")
    assert text.splitlines()[0] == ",".join(optout.COLUMNS)
    reloaded = optout.load(path)
    assert reloaded.available is True
    assert reloaded.contains("x-example.jp")


def test_a_registry_without_the_domain_column_is_not_usable(tmp_path):
    path = tmp_path / "optout.csv"
    path.write_text("company,note\nfoo,bar\n", encoding="utf-8")
    registry = optout.load(path)
    assert registry.available is False


# --------------------------------------------------------------------------
# 門3: 訂正窓口
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["", None, "not-an-address"])
def test_no_correction_contact_means_no_plan(tmp_path, value):
    """**指摘する側が訂正を受け付けないのは一方的である。**"""
    with pytest.raises(plan.PlanBlockedError) as exc:
        _plan(tmp_path, [_target()], correction_contact=value)
    assert "訂正" in str(exc.value)


def test_the_correction_window_is_at_least_thirty_days(tmp_path):
    with pytest.raises(template.TemplateError):
        template.render(
            "target-example.jp",
            template.findings_from_fact(FACT),
            sender_domain=SENDER,
            detail_url=URLS["detail_url"].format(domain="target-example.jp"),
            method_url=URLS["method_url"],
            correction_contact="c@obs.example.org",
            measured_month="2026-08",
            correction_days=29,
        )


def test_the_earliest_publish_date_is_after_the_correction_window(tmp_path):
    result = _plan(tmp_path, [_target()], correction_days=60)
    assert result.earliest_publish == dt.date(2026, 10, 3)
    assert any("tier2.enabled を true にしない" in n for n in result.notes)


# --------------------------------------------------------------------------
# 門4: 文面
# --------------------------------------------------------------------------


def test_the_body_carries_a_url_on_the_senders_own_domain(tmp_path):
    result = _plan(tmp_path, [_target()])
    body = result.messages[0].body
    assert "https://obs.example.org/companies/target-example.jp" in body
    assert "https://obs.example.org/method" in body


@pytest.mark.parametrize(
    "url",
    [
        "http://obs.example.org/companies",  # https でない
        "https://bit.ly/xyz",  # 短縮 URL
        "https://obs.example.org.evil.test/c",  # 接尾の見せかけ
    ],
)
def test_a_url_outside_the_sender_domain_is_refused(url):
    """**受信側が真正性を確かめられない文面は送らない。**"""
    with pytest.raises(template.TemplateError):
        template.render(
            "target-example.jp",
            template.findings_from_fact(FACT),
            sender_domain=SENDER,
            detail_url=url,
            method_url=URLS["method_url"],
            correction_contact="c@obs.example.org",
            measured_month="2026-08",
        )


def test_sales_language_is_refused():
    """**広告宣伝性がないという整理は、文面が実際にそうであることに依存する。**"""
    findings = [
        template.Finding(
            code="x", statement="SPF の設定についてご提案があります"
        )
    ]
    with pytest.raises(template.TemplateError) as exc:
        template.render(
            "target-example.jp",
            findings,
            sender_domain=SENDER,
            detail_url=URLS["detail_url"].format(domain="target-example.jp"),
            method_url=URLS["method_url"],
            correction_contact="c@obs.example.org",
            measured_month="2026-08",
        )
    assert "営業要素" in str(exc.value)


def test_the_body_is_held_to_the_public_sites_vocabulary(tmp_path):
    """通知だけ表現を緩めない。公開サイトと同じ検査を通す。"""
    result = _plan(tmp_path, [_target()])
    message = result.messages[0]
    assert vocabulary.check(message.body) == []
    assert vocabulary.check(message.subject) == []
    assert vocabulary.has_disclaimer(message.body)


def test_assertive_language_in_a_finding_is_refused():
    findings = [template.Finding(code="x", statement="この設定は危険です")]
    with pytest.raises(template.TemplateError) as exc:
        template.render(
            "target-example.jp",
            findings,
            sender_domain=SENDER,
            detail_url=URLS["detail_url"].format(domain="target-example.jp"),
            method_url=URLS["method_url"],
            correction_contact="c@obs.example.org",
            measured_month="2026-08",
        )
    assert "語彙規約" in str(exc.value)


def test_the_body_tells_the_reader_how_to_opt_out(tmp_path):
    result = _plan(tmp_path, [_target()], optout_contact="optout@obs.example.org")
    body = result.messages[0].body
    assert "optout@obs.example.org" in body
    assert "不要" in body


def test_the_body_says_it_is_not_advertising(tmp_path):
    body = _plan(tmp_path, [_target()]).messages[0].body
    assert "広告" in body and "含みません" in body


def test_no_findings_means_no_message():
    with pytest.raises(template.TemplateError):
        template.render(
            "target-example.jp",
            [],
            sender_domain=SENDER,
            detail_url=URLS["detail_url"].format(domain="target-example.jp"),
            method_url=URLS["method_url"],
            correction_contact="c@obs.example.org",
            measured_month="2026-08",
        )


# --------------------------------------------------------------------------
# 事実の抽出（原則2・原則5）
# --------------------------------------------------------------------------


def test_an_unobserved_fact_produces_nothing():
    """**取れなかったものを指摘しない。** 相手にとって事実に反する。"""
    assert template.findings_from_fact({"observed": False, "spf_present": None}) == []


def test_dkim_not_found_is_not_reported_as_not_configured():
    findings = template.findings_from_fact({"observed": True, "dkim_status": "not_found"})
    statement = next(f.statement for f in findings if f.code == "dkim_not_found")
    assert "設定していないことの確認ではありません" in statement


def test_spf_absent_is_worded_as_what_was_actually_seen():
    findings = template.findings_from_fact({"observed": True, "spf_present": False})
    statement = next(f.statement for f in findings if f.code == "spf_absent").strip()
    assert "取得しましたが" in statement


def test_findings_carry_the_standard_they_rest_on():
    findings = template.findings_from_fact(
        {
            "observed": True,
            "spf_present": True,
            "spf_valid": True,
            "spf_exceeds_limit": True,
            "spf_lookup_count": 12,
        }
    )
    limit = next(f for f in findings if f.code == "spf_lookup_limit")
    assert limit.standard == "RFC 7208 §4.6.4"
    assert "12" in limit.statement


def test_no_inference_reaches_the_notification():
    """**推察は通知に載せない。** 外れたときに文面全体の信用が落ちる。"""
    findings = template.findings_from_fact(
        {"observed": True, "spf_present": False, "vendor": "SomeESP"}
    )
    assert all("SomeESP" not in f.statement for f in findings)


# --------------------------------------------------------------------------
# 候補の篩い
# --------------------------------------------------------------------------


def test_unobserved_targets_are_skipped_with_a_reason(tmp_path):
    target = _target(fact={"observed": False})
    result = _plan(tmp_path, [target])
    assert result.count == 0
    assert result.skipped[0].reason == "未観測"


def test_targets_without_findings_are_skipped(tmp_path):
    clean = {
        "observed": True,
        "spf_present": True,
        "spf_valid": True,
        "spf_all_qualifier": "-",
        "dmarc_present": True,
        "effective_7489": "reject",
        "dmarc_rua": ["mailto:r@target-example.jp"],
        "dkim_status": "detected",
    }
    target = plan.NotifyTarget(
        "target-example.jp", clean, _contacts("target-example.jp")
    )
    result = _plan(tmp_path, [target])
    assert result.count == 0
    assert result.skipped[0].reason == "指摘事項なし"


def test_a_target_without_any_contact_is_skipped(tmp_path):
    target = _target(contacts=contacts.ContactSet(domain="target-example.jp"))
    result = _plan(tmp_path, [target])
    assert result.count == 0
    assert result.skipped[0].reason == "連絡先なし"


def test_a_published_web_only_channel_is_not_mailed_instead(tmp_path):
    """**公示された窓口を無視して `security@` を叩かない。**

    cloudflare.com の security.txt は実際に `Contact:` が HackerOne と
    自社フォームの2件で mailto が無い。この状態でエイリアスに送るのは、
    組織が公示した窓口の否定になる。
    """
    cs = _contacts("target-example.jp")
    cs.security_txt_found = True
    cs.web_contacts = ["https://hackerone.com/example"]
    assert cs.prefers_web is True
    result = _plan(tmp_path, [_target(contacts=cs)])
    assert result.count == 0
    assert result.skipped[0].reason == "公示窓口がメール以外"
    # **窓口が無いことにはしない。** 人が手で出す先として残る
    assert result.manual[0].urls == ["https://hackerone.com/example"]
    assert "人が手で出す先" in result.to_markdown()


def test_a_mailto_in_security_txt_wins_over_the_alias(tmp_path):
    cs = contacts.ContactSet(domain="target-example.jp")
    cs.security_txt_found = True
    cs.candidates.append(
        contacts.ContactCandidate(
            address="psirt@target-example.jp", source="security_txt", confidence="high"
        )
    )
    cs.candidates.append(
        contacts.ContactCandidate(
            address="security@target-example.jp", source="rfc2142", confidence="low"
        )
    )
    assert cs.prefers_web is False
    result = _plan(tmp_path, [_target(contacts=cs)])
    assert result.messages[0].address == "psirt@target-example.jp"


# --------------------------------------------------------------------------
# security.txt の解釈（RFC 9116）
# --------------------------------------------------------------------------


def test_parse_security_txt_separates_mail_from_web():
    text = (
        "Contact: https://hackerone.com/example\n"
        "Contact: mailto:psirt@example.jp\n"
        "Expires: 2027-01-01T00:00:00.000Z\n"
    )
    addresses, web, expires = contacts.parse_security_txt(text)
    assert addresses == ["psirt@example.jp"]
    assert web == ["https://hackerone.com/example"]
    assert expires.startswith("2027-01-01")


def test_a_web_only_security_txt_yields_no_mail_target():
    text = "Contact: https://example.jp/report\nContact: http://example.jp/abuse\n"
    addresses, web, _ = contacts.parse_security_txt(text)
    assert addresses == []
    assert len(web) == 2


def test_rfc2142_candidates_are_never_marked_verified():
    """**SMTP で存在確認するのは迷惑行為である。** 確認済みとは呼ばない。"""
    cs = contacts.discover("target-example.jp", fetch_https=False)
    assert cs.candidates
    assert all(c.verified is False for c in cs.candidates)
    assert {c.source for c in cs.candidates} == {"rfc2142"}


def test_not_fetching_https_is_recorded_as_not_looked(tmp_path):
    cs = contacts.discover("target-example.jp", fetch_https=False)
    assert cs.security_txt_found is False
    assert any("確認していない" in n for n in cs.notes)


def test_the_best_candidate_prefers_the_published_window():
    cs = contacts.discover(
        "target-example.jp", fetch_https=False, rdap_abuse="abuse@registrar.test"
    )
    assert cs.best is not None
    assert cs.best.address == "abuse@registrar.test"


# --------------------------------------------------------------------------
# 計画そのもの
# --------------------------------------------------------------------------


def test_the_plan_never_claims_to_be_sendable(tmp_path):
    """**送信の可否はこのモジュールの外の判断である。**"""
    result = _plan(tmp_path, [_target()])
    assert result.sendable is False
    assert "送信を行っていない" in result.to_markdown()


def test_the_plan_paces_at_one_message_per_second(tmp_path):
    targets = [_target(f"d{i}-example.jp", entity_id=f"E{i}") for i in range(5)]
    result = _plan(tmp_path, targets)
    assert result.count == 5
    assert [m.send_after_sec for m in result.messages] == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert result.duration_sec == 4.0


def test_the_plan_states_the_measured_remediation_rate(tmp_path):
    """**送れば直るという前提で運用を組ませない。**"""
    result = _plan(tmp_path, [_target()])
    expected = result.expectations()
    assert expected["remediation_rate_2w"] == pytest.approx(0.033)
    assert expected["reach_rate"] == pytest.approx(0.2416)
    assert "3.3%" in result.to_markdown()


def test_the_plan_records_the_self_check_and_the_registry(tmp_path):
    result = _plan(tmp_path, [_target()])
    payload = result.to_dict()
    assert payload["self_check"]["compliant"] is True
    assert payload["optout"]["available"] is True
    assert payload["sendable"] is False


def test_the_module_has_no_send_path():
    """**メールを送る経路をコードに持たせない。**"""
    import mailauth.p9_notify as pkg

    for name in ("send", "send_all", "deliver", "smtp", "sendmail"):
        assert not hasattr(pkg, name), f"{name} は持たない"
    assert "送らない" in (pkg.__doc__ or "")


# --------------------------------------------------------------------------
# ランナーと設定（configs/publish.yaml の notify 節）
# --------------------------------------------------------------------------


def _write_config(tmp_path, **overrides) -> str:
    import yaml

    notify = {
        "sender_domain": SENDER,
        "sender_dkim_selectors": ["sel1"],
        "detail_url": URLS["detail_url"],
        "method_url": URLS["method_url"],
        "optout_registry": str(tmp_path / "optout.csv"),
        "correction_days": 60,
        "rate_per_sec": 1,
        "fetch_security_txt": False,
    }
    notify.update(overrides.pop("notify", {}))
    payload = {
        "notify": notify,
        "tier2": {
            "correction_contact": overrides.pop(
                "correction_contact", "corrections@obs.example.org"
            )
        },
    }
    path = tmp_path / "publish.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    (tmp_path / "optout.csv").write_text(
        ",".join(optout.COLUMNS) + "\n", encoding="utf-8"
    )
    return str(path)


def _seed_run(run_id: str = "2026-08") -> None:
    """P1 / P3 / P5 の出力だけを置く。P9 はこの3つしか読まない。"""
    import pandas as pd

    from mailauth.paths import phase_dir

    for phase, filename, rows in (
        (
            "p1_population",
            "entities.parquet",
            [{"entity_id": "E1", "name": "株式会社テスト"}],
        ),
        (
            "p3_domains",
            "domains.parquet",
            [{"domain_id": "D1", "domain": "target-example.jp", "entity_id": "E1"}],
        ),
        ("p5_parse", "facts.parquet", [{"domain_id": "D1", **FACT}]),
    ):
        out = phase_dir(run_id, phase)
        out.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_parquet(out / filename)


def test_the_runner_refuses_without_a_sender_domain(tmp_path):
    from mailauth.p9_notify import runner

    config = _write_config(tmp_path, notify={"sender_domain": None})
    _seed_run()
    with pytest.raises(plan.PlanBlockedError) as exc:
        runner.run("2026-08", config=config, self_check=_compliant())
    assert "sender_domain" in str(exc.value)


def test_the_runner_refuses_without_verifiable_urls(tmp_path):
    from mailauth.p9_notify import runner

    config = _write_config(tmp_path, notify={"detail_url": None})
    _seed_run()
    with pytest.raises(plan.PlanBlockedError) as exc:
        runner.run("2026-08", config=config, self_check=_compliant())
    assert "URL" in str(exc.value)


def test_the_runner_needs_silver(tmp_path):
    from mailauth.p9_notify import runner

    config = _write_config(tmp_path)
    with pytest.raises(runner.MissingInputError):
        runner.run("2026-08", config=config, self_check=_compliant())


def test_the_runner_writes_a_reviewable_plan(tmp_path):
    import json

    from mailauth.p9_notify import runner
    from mailauth.paths import phase_dir

    config = _write_config(tmp_path)
    _seed_run()
    result, written = runner.run(
        "2026-08", config=config, self_check=_compliant(), notified_on=dt.date(2026, 8, 4)
    )
    assert result.count == 1
    assert result.messages[0].domain == "target-example.jp"
    assert result.messages[0].subject.startswith("[事前通知]")

    out = phase_dir("2026-08", runner.PHASE)
    payload = json.loads((out / runner.PLAN_JSON).read_text(encoding="utf-8"))
    assert payload["sendable"] is False
    assert payload["earliest_publish"] == "2026-10-03"
    assert "送信を行っていない" in (out / runner.PLAN_MARKDOWN).read_text(encoding="utf-8")
    assert len(written["paths"]) == 2


def test_the_runner_reports_what_it_left_out(tmp_path):
    import pandas as pd

    from mailauth.p9_notify import runner
    from mailauth.paths import phase_dir

    config = _write_config(tmp_path)
    _seed_run()
    facts = phase_dir("2026-08", "p5_parse")
    pd.DataFrame(
        [
            {"domain_id": "D1", **FACT},
            {"domain_id": "D2", "observed": False},
        ]
    ).to_parquet(facts / "facts.parquet")
    domains = phase_dir("2026-08", "p3_domains")
    pd.DataFrame(
        [
            {"domain_id": "D1", "domain": "target-example.jp", "entity_id": "E1"},
            {"domain_id": "D2", "domain": "quiet-example.jp", "entity_id": "E1"},
        ]
    ).to_parquet(domains / "domains.parquet")
    result, _ = runner.run("2026-08", config=config, self_check=_compliant())
    assert result.count == 1
    assert any("観測できていない" in n for n in result.notes)


def test_a_limit_is_reported_rather_than_silently_applied(tmp_path):
    import pandas as pd

    from mailauth.p9_notify import runner
    from mailauth.paths import phase_dir

    config = _write_config(tmp_path)
    _seed_run()
    pd.DataFrame([{"domain_id": f"D{i}", **FACT} for i in range(4)]).to_parquet(
        phase_dir("2026-08", "p5_parse") / "facts.parquet"
    )
    pd.DataFrame(
        [
            {"domain_id": f"D{i}", "domain": f"d{i}-example.jp", "entity_id": "E1"}
            for i in range(4)
        ]
    ).to_parquet(phase_dir("2026-08", "p3_domains") / "domains.parquet")
    result, _ = runner.run("2026-08", config=config, self_check=_compliant(), limit=2)
    assert result.count == 2
    assert any("残りは計画に出ていない" in n for n in result.notes)


def test_the_shipped_config_does_not_enable_notification():
    """**同梱の設定では動かない。** 送る判断は人が設定して初めて成立する。"""
    from mailauth.p9_notify import runner

    config, _ = runner.load_config()
    assert config.sender_domain is None
    assert config.detail_url is None
    assert config.correction_days >= template.CORRECTION_DAYS_MINIMUM


def test_the_shipped_registry_is_readable_and_empty():
    """空でも「読めている」状態にしておく。無いと計画そのものが作れない。"""
    registry = optout.load()
    assert registry.available is True
    assert registry.entries == []


def test_p9_is_not_part_of_the_monthly_pipeline():
    """**通知を cron に混ぜない。** ある月の朝に誰も気付かないまま出る。"""
    from pathlib import Path

    from mailauth import PHASES
    from mailauth.p9_notify import runner

    assert runner.PHASE not in PHASES
    workflow = Path(".github/workflows/monthly.yml").read_text(encoding="utf-8")
    assert "notify-plan" not in workflow
