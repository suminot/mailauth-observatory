"""訂正申告の登録簿と訂正履歴ページ（DESIGN.md Sprint 9・10）。

**訂正窓口の常設は名誉毀損の抗弁における「公益目的」の立証材料である。**
窓口があることと申告が処理されていることは別なので、処理の記録が機械的に
出ることを検査する。

`p9_notify` の通知文面は「訂正の履歴は公開サイトに残します」と書いている。
その約束が実装として存在することも、ここで確かめる。
"""

from __future__ import annotations

import datetime as dt

import pytest
import yaml

from mailauth import corrections
from mailauth.p8_publish import vocabulary
from mailauth.paths import repo_root

NOW = dt.datetime(2026, 8, 4, tzinfo=dt.UTC)


def _write(tmp_path, entries: list[dict]):
    path = tmp_path / "corrections.yaml"
    path.write_text(
        yaml.safe_dump({"corrections": entries}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return corrections.load(path)


ACCEPTED = {
    "id": "C-2026-001",
    "received_on": "2026-07-20",
    "domain": "example-a.jp",
    "channel": "issue",
    "claim": "このドメインは当社のものではない",
    "our_observation": "P3 が official_url から候補として展開していた",
    "status": "accepted",
    "reviewed_on": "2026-07-21",
    "resolution": "誤りを確認し、除外リストに追加した",
    "applied_from": "2026-08",
    "months_affected": ["2026-06", "2026-07"],
}

REJECTED = {
    "id": "C-2026-002",
    "received_on": "2026-08-02",
    "domain": "example-b.co.jp",
    "channel": "email",
    "claim": "DKIM は設定しているのに未検出になっている",
    "our_observation": "既知セレクタでは見つからず dkim_status=not_found",
    "status": "rejected",
    "reviewed_on": "2026-08-03",
    "resolution": "観測は「既知セレクタでは見つからなかった」であり未設定とは記載していない",
}

OPEN_OVERDUE = {
    "id": "C-2026-003",
    "received_on": "2026-07-01",
    "domain": "example-c.jp",
    "status": "under_review",
    "claim": "業種の分類が実態と違う",
}


# --------------------------------------------------------------------------
# 読めなかったことを 0 件にしない
# --------------------------------------------------------------------------


def test_a_missing_registry_is_not_zero_claims(tmp_path):
    """**「読めなかった」を「申告0件」として公開しない。**"""
    registry = corrections.load(tmp_path / "nope.yaml")
    assert registry.available is False
    assert registry.entries == []
    page = corrections.to_markdown(registry, now=NOW)
    assert "登録簿を読めていません" in page
    assert "「申告が0件」ではありません" in page
    assert "申告はありません" not in page


def test_an_empty_registry_says_so_explicitly(tmp_path):
    registry = _write(tmp_path, [])
    assert registry.available is True
    page = corrections.to_markdown(registry, now=NOW)
    assert "登録簿は読めています" in page


def test_a_broken_entry_makes_the_registry_unavailable(tmp_path):
    """不備がある状態を「申告0件」として公開しない。"""
    registry = _write(tmp_path, [{"received_on": "2026-08-01"}])  # id が無い
    assert registry.available is False
    assert registry.problems


# --------------------------------------------------------------------------
# 個人情報を持たない
# --------------------------------------------------------------------------


def test_the_registry_has_no_column_for_the_reporter(tmp_path):
    """**列が無ければ入らない。** 氏名やメールアドレスの列は作らない。"""
    for forbidden in ("reporter", "reporter_email", "name", "email", "phone"):
        assert forbidden not in corrections.ALLOWED_FIELDS


def test_an_unexpected_column_is_refused(tmp_path):
    """想定外の列が紛れ込んだら弾く。申告者を特定する情報の混入を防ぐ。"""
    registry = _write(tmp_path, [{**ACCEPTED, "reporter_email": "a@example.jp"}])
    assert registry.available is False
    assert any("想定外の列" in p for p in registry.problems)
    assert registry.entries == []


def test_the_page_states_that_reporters_are_not_stored(tmp_path):
    registry = _write(tmp_path, [ACCEPTED])
    page = corrections.to_markdown(registry, now=NOW)
    assert "氏名やメールアドレスはこの登録簿に保存していません" in page


# --------------------------------------------------------------------------
# 隠さない
# --------------------------------------------------------------------------


def test_open_claims_appear_before_they_are_resolved(tmp_path):
    """**審査中のものも公開する。** 隠すと握り潰したと読まれる余地が残る。"""
    registry = _write(tmp_path, [OPEN_OVERDUE])
    page = corrections.to_markdown(registry, now=NOW)
    assert "C-2026-003" in page
    assert "審査中" in page


def test_rejected_claims_appear_too(tmp_path):
    """**却下も載せる。** 訂正した分だけでは申告総数が分からない。"""
    registry = _write(tmp_path, [ACCEPTED, REJECTED])
    page = corrections.to_markdown(registry, now=NOW)
    assert "C-2026-002" in page
    assert "訂正しなかった" in page
    assert registry.by_status() == {"accepted": 1, "rejected": 1}


def test_an_unwritten_conclusion_is_shown_as_unwritten(tmp_path):
    """空欄を空欄として出す。埋まっていないことが読み手に分かる形にする。"""
    registry = _write(tmp_path, [OPEN_OVERDUE])
    assert "（未記入）" in corrections.to_markdown(registry, now=NOW)


# --------------------------------------------------------------------------
# SLA
# --------------------------------------------------------------------------


def test_an_unreviewed_claim_past_the_limit_is_overdue(tmp_path):
    """「48〜72時間で審査」は書いただけでは守られない。機械で測る。"""
    registry = _write(tmp_path, [OPEN_OVERDUE])
    overdue = registry.overdue(now=NOW)
    assert [e.id for e in overdue] == ["C-2026-003"]
    assert "上限（72 時間）を超えている申告が 1 件" in corrections.to_markdown(
        registry, now=NOW
    )


def test_a_reviewed_claim_is_never_overdue(tmp_path):
    """審査済みなら受付が古くても超過にはならない。"""
    old = {**ACCEPTED, "received_on": "2025-01-01", "reviewed_on": "2026-07-21"}
    registry = _write(tmp_path, [old])
    assert registry.overdue(now=NOW) == []


def test_a_claim_within_the_limit_is_not_overdue(tmp_path):
    fresh = {**OPEN_OVERDUE, "received_on": "2026-08-03"}
    registry = _write(tmp_path, [fresh])
    assert registry.overdue(now=NOW) == []


def test_the_sla_matches_the_design(tmp_path):
    assert corrections.SLA_TARGET_HOURS == 48
    assert corrections.SLA_LIMIT_HOURS == 72


def test_a_closed_claim_must_record_when_it_was_reviewed(tmp_path):
    """**いつ審査したかを残さないと SLA が測れない。**"""
    registry = _write(tmp_path, [{**ACCEPTED, "reviewed_on": None}])
    assert registry.available is False
    assert any("reviewed_on" in p for p in registry.problems)


def test_a_review_before_the_claim_is_refused(tmp_path):
    registry = _write(tmp_path, [{**ACCEPTED, "reviewed_on": "2026-07-19"}])
    assert registry.available is False


# --------------------------------------------------------------------------
# 過去を書き換えない
# --------------------------------------------------------------------------


def test_the_page_says_past_months_are_not_rewritten(tmp_path):
    """**訂正は次回以降に反映し、過去の数字は書き換えない。**

    公開済みの値を引用した第三者の記述と食い違わないようにする。
    """
    registry = _write(tmp_path, [ACCEPTED])
    page = corrections.to_markdown(registry, now=NOW)
    assert "過去の月の数字は書き換えていません" in page
    assert "2026-08 の計測から" in page


# --------------------------------------------------------------------------
# 形式
# --------------------------------------------------------------------------


def test_a_multiline_field_stays_inside_its_list_item(tmp_path):
    """YAML の `|` で書いた複数行が構造の外にこぼれないこと。"""
    entry = {**REJECTED, "resolution": "1行目\n2行目\n3行目"}
    registry = _write(tmp_path, [entry])
    page = corrections.to_markdown(registry, now=NOW)
    assert "- 結論: 1行目 2行目 3行目" in page


def test_a_pipe_in_a_field_does_not_break_the_table(tmp_path):
    entry = {**REJECTED, "claim": "a | b"}
    registry = _write(tmp_path, [entry])
    page = corrections.to_markdown(registry, now=NOW)
    assert "a ／ b" in page


def test_the_page_passes_the_public_vocabulary_check(tmp_path):
    """訂正履歴も公開ページなので同じ語彙規約に従う。"""
    registry = _write(tmp_path, [ACCEPTED, REJECTED, OPEN_OVERDUE])
    page = corrections.to_markdown(registry, now=NOW)
    assert vocabulary.check(page) == []


def test_duplicate_ids_are_refused(tmp_path):
    registry = _write(tmp_path, [ACCEPTED, ACCEPTED])
    assert registry.available is False
    assert any("重複" in p for p in registry.problems)


def test_an_unknown_status_is_refused(tmp_path):
    registry = _write(tmp_path, [{**ACCEPTED, "status": "maybe"}])
    assert registry.available is False


def test_entries_are_listed_newest_first(tmp_path):
    registry = _write(tmp_path, [ACCEPTED, REJECTED, OPEN_OVERDUE])
    assert [e.id for e in registry.entries] == [
        "C-2026-002",
        "C-2026-001",
        "C-2026-003",
    ]


def test_for_domain_finds_the_claims_about_one_domain(tmp_path):
    registry = _write(tmp_path, [ACCEPTED, REJECTED])
    assert [e.id for e in registry.for_domain("EXAMPLE-A.JP")] == ["C-2026-001"]
    assert registry.for_domain("nobody.test") == []


def test_to_json_is_stable_and_carries_the_sla(tmp_path):
    import json

    registry = _write(tmp_path, [ACCEPTED, OPEN_OVERDUE])
    payload = json.loads(corrections.to_json(registry, now=NOW))
    assert payload["sla_limit_hours"] == 72
    assert payload["overdue"] == ["C-2026-003"]
    assert payload["available"] is True


# --------------------------------------------------------------------------
# 同梱物と第2層の門
# --------------------------------------------------------------------------


def test_the_shipped_registry_is_readable_and_empty():
    """空でも「読めている」状態にしておく。無いと履歴ページが警告になる。"""
    registry = corrections.load()
    assert registry.available is True
    assert registry.entries == []


def test_the_history_page_is_committed():
    """通知文面が「訂正の履歴は公開サイトに残します」と書いている。

    **その約束を果たすページが実在すること。**
    """
    page = repo_root() / "site" / "src" / "corrections-log.md"
    assert page.is_file()
    assert "訂正履歴" in page.read_text(encoding="utf-8")


def test_the_notification_promises_what_the_site_provides():
    from mailauth.p9_notify import template

    body = template.render(
        "target-example.jp",
        [template.Finding(code="x", statement="SPF レコードを観測しました")],
        sender_domain="obs.example.org",
        detail_url="https://obs.example.org/c",
        method_url="https://obs.example.org/m",
        correction_contact="c@obs.example.org",
        measured_month="2026-08",
    ).body
    assert "訂正の履歴は公開サイトに残します" in body
    assert (repo_root() / "site" / "src" / "corrections-log.md").is_file()


def test_an_overdue_claim_blocks_tier2_but_not_tier1(tmp_path, monkeypatch):
    """**第1層は止めない。第2層は止める。**

    第1層は個社を名指ししないので未審査の申告が残っていても実害が小さい。
    個社名付き明細を出す段で未審査の申告を放置するのは、訂正窓口を
    名目だけにすることになる。
    """
    import mailauth.p8_publish.runner as runner

    registry = _write(tmp_path, [OPEN_OVERDUE])
    assert registry.overdue()  # 受付が古いので現在時刻でも超過している

    monkeypatch.setattr(runner.corrections, "load", lambda: registry)

    # 第1層のみ ── 警告は出るが止まらない
    ok = runner.run("2026-08", dry_run=True, require_month=False)
    assert ok["status"] != "failed"
    assert any(w["code"] == "CORRECTIONS_OVERDUE" for w in ok["warnings"])

    # 第2層を要求すると止まる
    config = tmp_path / "publish.yaml"
    base = yaml.safe_load(
        (repo_root() / "configs" / "publish.yaml").read_text(encoding="utf-8")
    )
    base["tier2"] = {
        "enabled": True,
        "notified_on": "2026-01-01",
        "access_control_configured": True,
        "correction_contact": "c@obs.example.org",
    }
    config.write_text(yaml.safe_dump(base, allow_unicode=True), encoding="utf-8")
    with pytest.raises(runner.PublishBlockedError) as exc:
        runner.run("2026-08", dry_run=True, config=config, require_month=False)
    assert "C-2026-003" in str(exc.value)
