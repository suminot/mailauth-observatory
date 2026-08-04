"""DKIM セレクタの飽和曲線（DESIGN.md 7.2 画面5）。

L1/L2 で足りているかを判断する材料。L3（GPL-3.0 で無効）を有効化する
価値があるかもここで見る。曲線が寝ていれば辞書を増やしても無駄である。
"""

from __future__ import annotations

from mailauth.contracts import QueryPurpose
from mailauth.p4_measure.saturation import build, selector_from


def _dkim(domain: str, selector: str, *, present=True, observed=True) -> dict:
    return {
        "domain": domain,
        "purpose": QueryPurpose.DKIM,
        "query_name": f"{selector}._domainkey.{domain}",
        "query_type": "TXT",
        "observed": observed,
        "record_present": present,
    }


def test_selector_is_extracted_from_the_query_name():
    assert selector_from("sel1._domainkey.example.jp") == "sel1"
    assert selector_from("*._domainkey.example.jp") == "*"
    assert selector_from("example.jp") is None
    assert selector_from("") is None


def test_the_curve_counts_only_new_domains():
    """既に見つかっているドメインを再発見しても新規には数えない。"""
    records = [
        _dkim("a.example", "s1"),
        _dkim("b.example", "s1"),
        # s2 は a を再発見するだけ
        _dkim("a.example", "s2"),
        # s3 は新規1件
        _dkim("c.example", "s3"),
    ]
    curve = build(records, order=["s1", "s2", "s3"])

    assert [p.new_domains for p in curve.points] == [2, 0, 1]
    assert [p.cumulative_domains for p in curve.points] == [2, 2, 3]
    assert curve.detected_domains == 3


def test_dead_selectors_are_listed_but_not_removed():
    """1件も稼がなかったセレクタは候補として出すが、即座に消さない。

    別の月には効くことがある。
    """
    curve = build(
        [_dkim("a.example", "s1"), _dkim("a.example", "s2")], order=["s1", "s2"]
    )
    assert curve.dead_selectors == ["s2"]
    assert "即座に消さない" in " / ".join(curve.notes)


def test_probed_domains_include_those_without_dkim():
    """分母は「セレクタを投げたドメイン」。検出できた数と分けて持つ。"""
    records = [
        _dkim("a.example", "s1"),
        _dkim("b.example", "s1", present=False),
        _dkim("c.example", "s1", observed=False, present=None),
    ]
    curve = build(records)
    assert curve.probed_domains == 3
    assert curve.detected_domains == 1


def test_control_and_wildcard_queries_are_ignored():
    """対照クエリが当たることは偽陽性の証拠であって、有効なセレクタではない。"""
    records = [
        _dkim("a.example", "s1"),
        {
            "domain": "a.example",
            "purpose": QueryPurpose.DKIM_CONTROL,
            "query_name": "nonexistent-control._domainkey.a.example",
            "observed": True,
            "record_present": True,
        },
        {
            "domain": "a.example",
            "purpose": QueryPurpose.DKIM_WILDCARD,
            "query_name": "*._domainkey.a.example",
            "observed": True,
            "record_present": True,
        },
    ]
    curve = build(records)
    assert curve.selectors_tried == 1
    assert curve.points[0].selector == "s1"


def test_coverage_and_selectors_for_a_fraction():
    records = [_dkim(f"d{i}.example", "s1") for i in range(9)]
    records.append(_dkim("d9.example", "s2"))
    curve = build(records, order=["s1", "s2"])

    assert curve.coverage_at(1) == 0.9
    assert curve.coverage_at(2) == 1.0
    assert curve.selectors_for(0.9) == 1
    assert curve.selectors_for(1.0) == 2


def test_no_dkim_says_it_is_not_proof_of_absence():
    """既知セレクタで見つからないことは未設定の証明ではない（原則5）。"""
    curve = build([_dkim("a.example", "s1", present=False)])
    assert curve.points == []
    assert curve.detected_domains == 0
    assert "未設定の証明ではない" in " / ".join(curve.notes)


def test_the_default_order_is_by_hit_count():
    """順序を渡さなければ当たった数の多い順にする。"""
    records = [
        _dkim("a.example", "rare"),
        _dkim("b.example", "common"),
        _dkim("c.example", "common"),
    ]
    curve = build(records)
    assert [p.selector for p in curve.points] == ["common", "rare"]


def test_the_curve_declares_its_order_dependence():
    """順序に依存する指標であることを注記に残す。"""
    curve = build([_dkim("a.example", "s1")])
    assert "順序に依存する" in " / ".join(curve.notes)
