"""未知 MX ホストの月次作業リスト（DESIGN.md Sprint 10）。

Sprint 10 は手動同定を「**月次のルーチンに組み込む**」と定めている。
一覧が出るだけでは routine にならないので、月をまたいだ追跡を検査する。

守らせたいこと
  1. 何か月連続で未知のままかを数える
  2. **調査して同定できなかったホストを未着手と区別する**（件数は残す）
  3. **一覧から消えたことを「同定できた」と読まない**（原則5）
"""

from __future__ import annotations

import json

import pytest
import yaml

from mailauth import worklist
from mailauth.paths import phase_dir, repo_root


def _seed(run_id: str, hosts: list[dict]) -> None:
    d = phase_dir(run_id, "p6_infer")
    d.mkdir(parents=True, exist_ok=True)
    (d / "_manifest.json").write_text(
        json.dumps({"run_id": run_id, "breakdown": {"unknown_mx_hosts": hosts}}),
        encoding="utf-8",
    )


def _host(name: str, count: int = 10) -> dict:
    return {"registered_domain": name, "count": count, "examples": [f"mx1.{name}"]}


def _unidentified(tmp_path, entries: list[dict]):
    path = tmp_path / "unidentified.yaml"
    path.write_text(yaml.safe_dump({"hosts": entries}, allow_unicode=True), "utf-8")
    return path


# --------------------------------------------------------------------------
# 連続月数
# --------------------------------------------------------------------------


def test_a_host_seen_every_month_accumulates_a_streak(tmp_path):
    _seed("2026-06", [_host("old.jp", 40)])
    _seed("2026-07", [_host("old.jp", 38)])
    _seed("2026-08", [_host("old.jp", 35)])
    result = worklist.build(
        "2026-08",
        months=["2026-06", "2026-07", "2026-08"],
        unidentified_path=_unidentified(tmp_path, []),
    )
    assert [h.months_unknown for h in result.hosts] == [3]
    assert result.hosts[0].is_stale is True


def test_a_new_host_starts_at_one_month(tmp_path):
    _seed("2026-07", [_host("old.jp", 40)])
    _seed("2026-08", [_host("old.jp", 38), _host("new.jp", 12)])
    result = worklist.build(
        "2026-08",
        months=["2026-07", "2026-08"],
        unidentified_path=_unidentified(tmp_path, []),
    )
    streaks = {h.registered_domain: h.months_unknown for h in result.hosts}
    assert streaks == {"old.jp": 2, "new.jp": 1}
    assert result.stale == []


def test_a_month_without_a_p6_manifest_does_not_break_the_streak(tmp_path):
    """**実行記録が無い月を「載っていなかった」とは扱わない。**"""
    _seed("2026-06", [_host("old.jp", 40)])
    # 2026-07 は実行記録が無い
    _seed("2026-08", [_host("old.jp", 35)])
    result = worklist.build(
        "2026-08",
        months=["2026-06", "2026-07", "2026-08"],
        unidentified_path=_unidentified(tmp_path, []),
    )
    assert result.hosts[0].months_unknown == 2


def test_absence_from_a_truncated_list_is_not_read_as_identified(tmp_path):
    """**一覧から消えたことは同定できた証拠ではない。**

    P6 の未知ホスト一覧は上位 N 件で切っている。順位が落ちて消えたホストと、
    辞書に載って消えたホストは区別できない（原則5）。
    """
    _seed("2026-07", [_host(f"filler{i}.jp", 100 - i) for i in range(worklist.TOP_N)])
    _seed("2026-08", [_host("x.jp", 50)])
    result = worklist.build(
        "2026-08",
        months=["2026-07", "2026-08"],
        unidentified_path=_unidentified(tmp_path, []),
    )
    host = result.hosts[0]
    assert host.streak_uncertain is True
    assert any("同定できた」と読めない" in n for n in result.notes)
    assert "以上" in worklist.to_markdown(result)


def test_absence_from_a_short_list_is_a_real_gap(tmp_path):
    """一覧が上限に達していなければ、そこに無いことは意味を持つ。"""
    _seed("2026-07", [_host("other.jp", 5)])
    _seed("2026-08", [_host("x.jp", 50)])
    result = worklist.build(
        "2026-08",
        months=["2026-07", "2026-08"],
        unidentified_path=_unidentified(tmp_path, []),
    )
    assert result.hosts[0].streak_uncertain is False


def test_a_full_current_list_is_reported_as_truncated(tmp_path):
    """**一覧に無いホストが残っていることを黙らない。**"""
    _seed("2026-08", [_host(f"h{i}.jp", 100 - i) for i in range(worklist.TOP_N)])
    result = worklist.build(
        "2026-08", months=["2026-08"], unidentified_path=_unidentified(tmp_path, [])
    )
    assert result.truncated is True
    assert any("上限" in n for n in result.notes)


# --------------------------------------------------------------------------
# 調査済みと未着手を分ける
# --------------------------------------------------------------------------


def test_an_investigated_host_leaves_the_worklist_but_keeps_its_count(tmp_path):
    """**「調べたが分からなかった」は結論であって、無かったことにしない。**"""
    _seed("2026-08", [_host("self.jp", 40), _host("todo.jp", 12)])
    path = _unidentified(
        tmp_path,
        [
            {
                "registered_domain": "self.jp",
                "reason": "self_hosted",
                "investigated_on": "2026-07-10",
                "note": "自社運用",
            }
        ],
    )
    result = worklist.build("2026-08", months=["2026-08"], unidentified_path=path)
    assert [h.registered_domain for h in result.hosts] == ["todo.jp"]
    assert [h.registered_domain for h in result.set_aside] == ["self.jp"]
    assert result.set_aside[0].count == 40

    page = worklist.to_markdown(result, unidentified=worklist.load_unidentified(path)[0])
    assert "調査済み・同定できなかったもの（1 件）" in page
    assert "自社運用。製品ではない" in page
    assert "2026-07-10" in page


def test_an_unreadable_investigation_record_puts_everything_back_on_the_list(tmp_path):
    """読めなければ全ホストを未着手として出す。**作業が増える側に倒す。**"""
    _seed("2026-08", [_host("self.jp", 40)])
    result = worklist.build(
        "2026-08", months=["2026-08"], unidentified_path=tmp_path / "nope.yaml"
    )
    assert result.unidentified_available is False
    assert [h.registered_domain for h in result.hosts] == ["self.jp"]
    assert result.set_aside == []
    assert "すべて未着手として並べています" in worklist.to_markdown(result)


def test_an_unknown_reason_falls_back_to_other(tmp_path):
    _seed("2026-08", [_host("x.jp", 5)])
    path = _unidentified(
        tmp_path, [{"registered_domain": "x.jp", "reason": "なんとなく"}]
    )
    entries, available, notes = worklist.load_unidentified(path)
    assert available is True
    assert entries["x.jp"].reason == "other"
    assert any("reason が不正" in n for n in notes)


def test_a_record_without_a_domain_is_reported(tmp_path):
    path = _unidentified(tmp_path, [{"reason": "self_hosted"}])
    entries, available, notes = worklist.load_unidentified(path)
    assert entries == {}
    assert available is True
    assert any("registered_domain が無い" in n for n in notes)


def test_a_bad_investigation_date_does_not_drop_the_record(tmp_path):
    """日付が読めなくても作業リストから外す判定は効かせる。"""
    path = _unidentified(
        tmp_path,
        [{"registered_domain": "x.jp", "reason": "reseller", "investigated_on": "いつか"}],
    )
    entries, _, notes = worklist.load_unidentified(path)
    assert "x.jp" in entries
    assert entries["x.jp"].investigated_on is None
    assert any("日付として読めない" in n for n in notes)


# --------------------------------------------------------------------------
# 出力
# --------------------------------------------------------------------------


def test_the_page_marks_stale_hosts(tmp_path):
    for month in ("2026-06", "2026-07", "2026-08"):
        _seed(month, [_host("old.jp", 40)])
    result = worklist.build(
        "2026-08",
        months=["2026-06", "2026-07", "2026-08"],
        unidentified_path=_unidentified(tmp_path, []),
    )
    page = worklist.to_markdown(result)
    assert "⚠" in page
    assert f"{worklist.STALE_MONTHS} か月以上そのまま" in page


def test_the_page_says_past_months_are_not_rebuilt(tmp_path):
    """辞書を足しても過去の月は作り直さない（訂正の方針と同じ）。"""
    _seed("2026-08", [_host("x.jp", 5)])
    page = worklist.to_markdown(
        worklist.build(
            "2026-08", months=["2026-08"], unidentified_path=_unidentified(tmp_path, [])
        )
    )
    assert "過去の月は作り直しません" in page


def test_an_empty_month_is_explained(tmp_path):
    result = worklist.build(
        "2026-08", months=["2026-08"], unidentified_path=_unidentified(tmp_path, [])
    )
    assert result.hosts == []
    assert any("未知ホストが無い" in n for n in result.notes)


def test_to_dict_carries_the_threshold_and_the_caveats(tmp_path):
    _seed("2026-08", [_host("x.jp", 5)])
    payload = worklist.build(
        "2026-08", months=["2026-08"], unidentified_path=_unidentified(tmp_path, [])
    ).to_dict()
    assert payload["stale_months_threshold"] == worklist.STALE_MONTHS
    assert payload["truncated"] is False
    assert payload["unidentified_available"] is True


# --------------------------------------------------------------------------
# 同梱物と月次ルーチン
# --------------------------------------------------------------------------


def test_the_shipped_record_is_readable_and_empty():
    entries, available, _ = worklist.load_unidentified()
    assert available is True
    assert entries == {}


def test_the_top_n_matches_p6():
    """P6 が出す件数と食い違うと、上限判定が嘘になる。"""
    from mailauth.p6_infer.runner import UNKNOWN_MX_TOP_N

    assert worklist.TOP_N == UNKNOWN_MX_TOP_N


def test_the_monthly_workflow_builds_the_worklist():
    """**一覧が出るだけでは routine にならない。** 月次で作らせる。"""
    text = (repo_root() / ".github/workflows/monthly.yml").read_text(encoding="utf-8")
    assert "mailauth worklist" in text
    # 作ったものを残さないと翌月に連続月数が数えられない
    assert "runs" in text


@pytest.mark.parametrize("reason", worklist.UNIDENTIFIED_REASONS)
def test_every_reason_has_a_label(reason):
    """理由を分類しておく。自社運用と情報が無いのは別の話である。"""
    assert worklist.REASON_LABELS.get(reason)


def test_the_record_is_not_inside_the_fingerprint_dictionary_directory():
    """**フィンガープリント辞書の glob に混ざらせない。**

    `configs/fingerprints/*.yaml` は P6 が規則として読む。ここに規則でない
    ファイルを置くと、辞書として数えられ、コンソールの辞書一覧にも出る。
    """
    assert "configs/fingerprints/" not in worklist.UNIDENTIFIED_PATH
    assert (repo_root() / worklist.UNIDENTIFIED_PATH).is_file()

    from mailauth.p6_infer.fingerprints import load_all

    loaded = set(load_all().versions)
    assert not any("unidentified" in name for name in loaded), (
        f"規則でないファイルが辞書として読まれている: {sorted(loaded)}"
    )
