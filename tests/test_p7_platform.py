"""P7 のメール基盤・前段の集計（DESIGN-platform.md §1、§6）。

検証の主眼は3つ。
  1. **推定が1件あることと、使っていることは別である**（数えない行が3種類ある）
  2. 分母が2つ出ること。片方だけだと誤読される
  3. ベンダー名も秘匿の対象になること。**件数は残す**
"""

from __future__ import annotations

import datetime as dt
import json

from mailauth.contracts import (
    MIN_CELL_SIZE,
    SUPPRESSED_VENDOR,
    InferenceCategory,
    InferenceLayer,
    UndetectableReason,
)
from mailauth.p6_infer.match import NOT_DETECTED_VENDOR
from mailauth.p7_aggregate import platform as platform_mod

MONTH = dt.date(2026, 9, 1)


def _inf(**kwargs) -> dict:
    """既定は「普通に数えてよい1件」。"""
    base = {
        "domain_id": "d:1",
        "entity_id": "jp:1",
        "category": InferenceCategory.MAIL_PLATFORM,
        "vendor": "Microsoft",
        "layer": InferenceLayer.PLATFORM,
        "is_layer_primary": True,
        "undetectable_reason": None,
        "engine": None,
        "evidence": json.dumps(
            [{"record_type": "MX", "matched_value": "x", "rule_id": "m365-mx-01"}]
        ),
    }
    base.update(kwargs)
    return base


def _agg(rows, members=None, **kwargs):
    return platform_mod.aggregate_platform(
        rows,
        measured_month=MONTH,
        population_id="jp-all-listed",
        members=members if members is not None else {"jp:1"},
        observed_domains=kwargs.pop("observed_domains", 100),
        observed_entities=kwargs.pop("observed_entities", 80),
        threshold=kwargs.pop("threshold", 1),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 数えない行
# ---------------------------------------------------------------------------


def test_the_sentinel_row_is_not_a_vendor():
    """`not_detected` は盲点を示す番兵で、ベンダーではない。

    **層を持たせても数えないこと。** いまの番兵は層も理由も持たないので
    二重に除かれるが、「受信の前段は検出できなかった」と層ごとに出すよう
    にした瞬間、名前で除く条件だけが残る。そこを確かめる。
    """
    assert _agg([_inf(vendor=NOT_DETECTED_VENDOR, layer=None)]) == []
    assert (
        _agg(
            [
                _inf(
                    vendor=NOT_DETECTED_VENDOR,
                    layer=InferenceLayer.INBOUND_GATEWAY,
                    undetectable_reason=None,
                )
            ]
        )
        == []
    )


def test_a_row_with_a_reason_is_not_counted():
    """**ベンダーが分かっても、使っているとは言えない痕跡がある。**

    Microsoft の仮 MX がそれで、数えると「①受信している」に化ける。
    """
    rows = _agg(
        [_inf(undetectable_reason=UndetectableReason.TENANT_PLACEHOLDER_MX)]
    )
    assert rows == []


def test_the_runner_up_in_a_layer_is_not_counted():
    """同じ層の代表でない行を数えると、合計が100%を超える。"""
    rows = _agg(
        [
            _inf(vendor="Mimecast", layer=InferenceLayer.INBOUND_GATEWAY),
            _inf(
                vendor="Cisco",
                layer=InferenceLayer.INBOUND_GATEWAY,
                is_layer_primary=False,
            ),
        ]
    )
    assert [r.vendor for r in rows] == ["Mimecast"]


def test_rows_outside_the_population_are_not_counted():
    """母集団の外の推定を混ぜると、分母と分子がずれる。"""
    rows = _agg([_inf(entity_id="jp:999")], members={"jp:1"})
    assert rows == []


# ---------------------------------------------------------------------------
# ①と② ── 1つに丸めない
# ---------------------------------------------------------------------------


def test_mx_evidence_counts_as_receiving():
    rows = _agg([_inf()])
    assert rows[0].domains_receiving == 1
    assert rows[0].domains_any == 1


def test_spf_only_evidence_is_not_receiving():
    """**送信の前段は MX を握らない。** 受信が 0 でも「使っていない」ではない。"""
    rows = _agg(
        [
            _inf(
                vendor="HENNGE",
                layer=InferenceLayer.OUTBOUND_GATEWAY,
                evidence=json.dumps(
                    [{"record_type": "SPF_INCLUDE", "matched_value": "x", "rule_id": "y"}]
                ),
            )
        ]
    )
    assert rows[0].domains_any == 1
    assert rows[0].domains_receiving == 0


def test_unreadable_evidence_is_not_called_receiving():
    """**読めないものを断定しない**（原則5）。"""
    rows = _agg([_inf(evidence="これは JSON ではない")])
    assert rows[0].domains_any == 1
    assert rows[0].domains_receiving == 0


# ---------------------------------------------------------------------------
# 分母
# ---------------------------------------------------------------------------


def test_both_denominators_are_carried():
    """**片方だけだと誤読される**（運営者の判断）。"""
    rows = _agg(
        [
            _inf(domain_id="d:1"),
            _inf(domain_id="d:2", vendor="Google"),
        ],
        observed_domains=100,
        observed_entities=80,
    )
    for row in rows:
        assert row.observed_domains == 100
        assert row.observed_entities == 80
        # 同定できたのは2ドメイン（分母のもう片方）
        assert row.identified_domains == 2


def test_the_identified_denominator_is_per_layer():
    """層をまたいで足すと、実基盤の分母に前段が混ざる。"""
    rows = _agg(
        [
            _inf(domain_id="d:1"),
            _inf(
                domain_id="d:2",
                vendor="Proofpoint",
                layer=InferenceLayer.INBOUND_GATEWAY,
            ),
        ]
    )
    by_layer = {r.layer: r.identified_domains for r in rows}
    assert by_layer[InferenceLayer.PLATFORM] == 1
    assert by_layer[InferenceLayer.INBOUND_GATEWAY] == 1


def test_a_domain_is_counted_once_per_vendor():
    """同じドメインに同じベンダーの推定が2件あっても1つ。"""
    rows = _agg([_inf(), _inf()])
    assert rows[0].domains_any == 1


# ---------------------------------------------------------------------------
# 秘匿
# ---------------------------------------------------------------------------


def test_small_vendors_are_bundled_but_their_count_survives():
    """**件数は残す。** 束ねた先の合計として出るので分母が閉じたまま。"""
    rows = _agg(
        [
            _inf(domain_id=f"d:{i}", entity_id=f"jp:{i}", vendor="Microsoft")
            for i in range(MIN_CELL_SIZE)
        ]
        + [_inf(domain_id="d:99", entity_id="jp:99", vendor="ごく小さいベンダー")],
        members={f"jp:{i}" for i in range(MIN_CELL_SIZE)} | {"jp:99"},
        threshold=MIN_CELL_SIZE,
    )
    named = {r.vendor: r for r in rows}
    assert "ごく小さいベンダー" not in named
    assert named[SUPPRESSED_VENDOR].entities_any == 1
    assert named[SUPPRESSED_VENDOR].suppressed is True
    # 分母は秘匿しても変わらない
    assert named[SUPPRESSED_VENDOR].identified_domains == MIN_CELL_SIZE + 1


def test_the_engine_is_kept_for_oem_rows():
    rows = _agg(
        [
            _inf(
                vendor="SBテクノロジー",
                layer=InferenceLayer.OUTBOUND_GATEWAY,
                engine="Active! gate SS",
            )
        ]
    )
    assert rows[0].engine == "Active! gate SS"
