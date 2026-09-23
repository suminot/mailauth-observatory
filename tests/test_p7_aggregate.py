"""P7 集計。

検証の主眼は DESIGN.md P7 の受け入れ基準3つ。
  1. 秘匿後の合計値から個社が逆算できないこと（2次秘匿）
  2. 企業数ベースとドメインベースの両方が出力されていること
  3. 前月差分が「消えた」と「取れなかった」を混同していないこと
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from mailauth.contracts import (
    ENTITY_ARROW_SCHEMA,
    FACT_ARROW_SCHEMA,
    INFERENCE_ARROW_SCHEMA,
    MIN_CELL_SIZE,
    SUPPRESSED_SECTOR_CODE,
    DkimStatus,
    EntityStatus,
    ParkClass,
    PolicyLabel,
)
from mailauth.io import read_parquet, write_parquet
from mailauth.p7_aggregate import MissingInputError, suppress
from mailauth.p7_aggregate import delta as delta_mod
from mailauth.p7_aggregate import run as run_p7
from mailauth.p7_aggregate.metrics import DomainRow, aggregate
from mailauth.paths import gold_dir, phase_output

RUN = "2026-08"
PREV = "2026-07"
MONTH = dt.date(2026, 8, 1)
POP = "jp-all-listed"


# ===========================================================================
# セル秘匿
# ===========================================================================


def test_cells_below_the_threshold_are_suppressed():
    plan = suppress.plan({"1": 40, "2": 30, "3": 2, "4": 1})
    assert plan.suppressed == ["3", "4"]
    assert plan.published == ["1", "2"]
    assert not plan.secondary


def test_a_single_suppressed_cell_pulls_in_a_second_one():
    """秘匿セルが1つだと合計から引き算で復元できる（2次秘匿）。"""
    plan = suppress.plan({"1": 100, "2": 40, "3": 2})
    assert plan.secondary == ["2"]
    assert plan.suppressed == ["2", "3"]
    assert plan.published == ["1"]
    assert "2次秘匿" in " / ".join(plan.notes)


def test_nothing_is_suppressed_when_all_cells_are_large():
    plan = suppress.plan({"1": 100, "2": 40, "3": 30})
    assert plan.suppressed == []
    assert plan.published == ["1", "2", "3"]


def test_recoverability_check():
    counts = {"1": 100, "2": 40, "3": 2}
    # 1つだけ秘匿 → 合計から引ける
    assert suppress.is_recoverable(counts, ["3"]) is True
    # 2つ以上でも合計が閾値未満なら候補が狭すぎる
    assert suppress.is_recoverable({"a": 1, "b": 2, "c": 50}, ["a", "b"]) is True
    assert suppress.is_recoverable(counts, ["2", "3"]) is False
    assert suppress.is_recoverable(counts, []) is False


def test_threshold_is_five():
    """k=5 が k-匿名性の実務値（DESIGN.md P7）。"""
    assert MIN_CELL_SIZE == 5
    assert suppress.plan({"a": 5, "b": 5, "c": 5}).suppressed == []
    assert suppress.plan({"a": 4, "b": 4, "c": 50}).suppressed == ["a", "b"]


# ===========================================================================
# 指標
# ===========================================================================


def _row(**kwargs) -> DomainRow:
    base = {
        "domain_id": "d:1",
        "entity_id": "jp:1",
        "observed": True,
        "spf_present": False,
        "dmarc_present": False,
        "effective_7489": None,
        "dmarc_p": None,
        "policy_label": None,
        "dkim_status": None,
        "mta_sts_present": False,
        "tls_rpt_present": False,
        "bimi_present": False,
        "bimi_has_vmc": False,
        "dnssec_signed": False,
        "dane_present": False,
        "dane_orphan": False,
    }
    base.update(kwargs)
    return DomainRow(**base)


def _agg(rows, total_entities=3):
    return aggregate(rows, measured_month=MONTH, population_id=POP, total_entities=total_entities)


def test_unobserved_domains_are_excluded_from_the_denominator():
    """取れなかったドメインを分母に入れると「未対応」に化ける（原則5）。"""
    stats = _agg(
        [
            _row(domain_id="d:1", spf_present=True),
            _row(domain_id="d:2", observed=False),
        ]
    )
    assert stats.total_domains == 2
    assert stats.observed_domains == 1
    assert stats.spf_adopted_domains == 1
    # 1/1 = 100%。1/2 = 50% としてはいけない


def test_both_entity_and_domain_bases_are_reported():
    """ドメイン数だけで語ると多ドメイン企業の重みが不当に増す。"""
    stats = _agg(
        [
            _row(domain_id="d:1", entity_id="jp:1", dmarc_present=True),
            _row(domain_id="d:2", entity_id="jp:1", dmarc_present=True),
            _row(domain_id="d:3", entity_id="jp:2", dmarc_present=True),
        ]
    )
    assert stats.dmarc_adopted_domains == 3
    assert stats.dmarc_adopted_entities == 2


def test_entity_denominator_includes_companies_without_domains():
    """ドメインを持つ企業だけを数えると採用率が実態より高く出る。"""
    stats = _agg([_row(entity_id="jp:1", spf_present=True)], total_entities=10)
    assert stats.total_entities == 10
    assert stats.spf_adopted_entities == 1


def test_nominal_and_enforced_reject_are_separated():
    """`p=reject` と書いてあることと、効いていることは別の事実。"""
    stats = _agg(
        [
            _row(
                domain_id="d:1",
                dmarc_p="reject",
                effective_7489="reject",
                policy_label=PolicyLabel.ENFORCED_REJECT,
            ),
            # pct=10 で名目のみ
            _row(
                domain_id="d:2",
                dmarc_p="reject",
                effective_7489="quarantine",
                policy_label=PolicyLabel.NOMINAL_REJECT_WEAK_PCT,
            ),
            # rua が無く可視性ゼロ
            _row(
                domain_id="d:3",
                dmarc_p="reject",
                effective_7489="reject",
                policy_label=PolicyLabel.BLIND_REJECT,
            ),
        ]
    )
    assert stats.nominal_reject_domains == 3
    assert stats.enforced_reject_domains == 1
    assert stats.blind_reject_domains == 1


def test_enforced_quarantine_is_not_counted_as_enforced_reject():
    """quarantine を reject の欄に混ぜない（ラベルの流用を戻した回帰）。"""
    stats = _agg(
        [
            _row(
                dmarc_p="quarantine",
                effective_7489="quarantine",
                policy_label=PolicyLabel.ENFORCED_QUARANTINE,
            )
        ]
    )
    assert stats.enforced_reject_domains == 0
    assert stats.nominal_reject_domains == 0
    # 強制していること自体は dmarc_enforced_* に出る
    assert stats.dmarc_enforced_domains == 1


def test_dkim_not_found_is_not_the_same_as_unset():
    stats = _agg(
        [
            _row(domain_id="d:1", dkim_status=DkimStatus.DETECTED),
            _row(domain_id="d:2", dkim_status=DkimStatus.NOT_FOUND_IN_KNOWN_SELECTORS),
            # 階層C はセレクタを投げていないので、どちらにも数えない
            _row(domain_id="d:3", dkim_status=DkimStatus.NOT_APPLICABLE),
        ]
    )
    assert stats.dkim_detected_domains == 1
    assert stats.dkim_not_found_domains == 1


def test_dane_orphan_is_excluded_from_valid():
    """TLSA があっても親ゾーンが未署名なら DANE は実効しない。"""
    stats = _agg(
        [
            _row(domain_id="d:1", dane_present=True, dnssec_signed=True),
            _row(domain_id="d:2", dane_present=True, dane_orphan=True),
        ]
    )
    assert stats.dane_domains == 2
    assert stats.dane_dnssec_valid == 1
    assert stats.dane_orphan == 1


def test_maturity_stage_distribution_covers_all_five_stages():
    stats = _agg(
        [
            _row(domain_id="d:1"),
            _row(
                domain_id="d:2",
                spf_present=True,
                dmarc_present=True,
                dkim_status=DkimStatus.DETECTED,
                effective_7489="reject",
            ),
        ]
    )
    dist = json.loads(stats.maturity_stage_dist)
    assert sorted(dist) == ["0", "1", "2", "3", "4"]
    assert dist["0"] == 1
    assert dist["2"] == 1


def test_park_metrics_split_sending_from_parked():
    rows = [
        _row(domain_id="d:1", park_class=ParkClass.ACTIVE_SENDING, effective_7489="reject"),
        _row(domain_id="d:2", park_class=ParkClass.HARDENED_PARKED),
        _row(domain_id="d:3", park_class=ParkClass.NEGLECTED),
        # 矛盾はパーク分母に入れない
        _row(domain_id="d:4", park_class=ParkClass.INCONSISTENT),
    ]
    stats = _agg(rows)
    assert stats.sending_domains == 1
    assert stats.sending_enforced == 1
    assert stats.parked_domains == 2
    assert stats.parked_hardened == 1
    assert stats.parked_neglected == 1
    assert stats.park_defense_rate == 0.5


def test_park_defense_rate_is_none_when_there_are_no_parked_domains():
    """0 と「分母が無い」を混ぜない。"""
    stats = _agg([_row(park_class=ParkClass.ACTIVE_SENDING)])
    assert stats.park_defense_rate is None


# ===========================================================================
# 前月差分
# ===========================================================================


def _fact(domain_id: str, **kwargs) -> dict:
    base = {
        "domain_id": domain_id,
        "entity_id": "jp:1",
        "observed": True,
        "record_present": True,
        "effective_7489": "none",
    }
    base.update(kwargs)
    return base


def test_unobserved_is_not_treated_as_disappeared():
    """SERVFAIL のドメインは消えていない（受け入れ基準）。"""
    result = delta_mod.compute(
        [_fact("d:1", observed=False, record_present=None)],
        [_fact("d:1")],
        before_previous=[_fact("d:1")],
    )
    assert result.domains_disappeared == 0
    assert result.domains_unobserved_this_month == 1


def test_policy_changes_are_not_judged_without_an_observation():
    result = delta_mod.compute(
        [_fact("d:1", observed=False, effective_7489=None)],
        [_fact("d:1", effective_7489="reject")],
    )
    assert result.policy_downgraded == 0


def test_policy_upgrade_and_downgrade():
    result = delta_mod.compute(
        [
            _fact("d:1", effective_7489="reject"),
            _fact("d:2", effective_7489="none"),
        ],
        [
            _fact("d:1", effective_7489="none"),
            _fact("d:2", effective_7489="quarantine"),
        ],
    )
    assert result.policy_upgraded == 1
    assert result.policy_downgraded == 1


def test_disappearance_needs_two_consecutive_absences():
    """1回の不在は一時的な DNS 障害と区別できない。"""
    absent = _fact("d:1", record_present=False)
    present = _fact("d:1")

    once = delta_mod.compute([absent], [present], before_previous=[present])
    assert once.domains_disappeared == 0

    twice = delta_mod.compute([absent], [absent], before_previous=[present])
    assert twice.domains_disappeared == 1


def test_delta_is_skipped_without_a_previous_month():
    result = delta_mod.compute([_fact("d:1")], None)
    assert result.skipped is True
    assert result.policy_upgraded == 0


def test_entity_churn():
    result = delta_mod.compute(
        [_fact("d:1")],
        [_fact("d:1")],
        current_entities={"a", "b"},
        previous_entities={"a", "c"},
    )
    assert result.entities_new == 1
    assert result.entities_removed == 1


# ===========================================================================
# P7 の通し
# ===========================================================================


def _write_entities(n_per_sector: dict[str, int], run_id: str = RUN) -> list[str]:
    rows = []
    ids: list[str] = []
    for code, count in sorted(n_per_sector.items()):
        for i in range(count):
            entity_id = f"jp:{code}-{i}"
            ids.append(entity_id)
            rows.append(
                {
                    "entity_id": entity_id,
                    "run_id": run_id,
                    "country": "JP",
                    "population_ids": [POP],
                    "name": f"{code}社{i}",
                    "name_normalized": f"{code}{i}",
                    "common12_code": code,
                    "common12_label": f"業種{code}",
                    "status": EntityStatus.ACTIVE,
                }
            )
    write_parquet(
        rows,
        phase_output(run_id, "p1_population", "entities.parquet"),
        ENTITY_ARROW_SCHEMA,
    )
    return ids


def _write_facts(entity_ids: list[str], run_id: str = RUN, **overrides) -> None:
    rows = []
    for i, entity_id in enumerate(entity_ids):
        row = {
            "fact_id": f"f:{i}",
            "domain_id": f"d:{entity_id}",
            "entity_id": entity_id,
            "run_id": run_id,
            "measured_month": dt.date(int(run_id[:4]), int(run_id[5:7]), 1),
            "observed": True,
            "record_present": True,
            "spf_present": True,
            "dmarc_present": True,
            "dmarc_p": "reject",
            "effective_7489": "reject",
            "policy_label": PolicyLabel.ENFORCED_REJECT,
        }
        row.update(overrides)
        rows.append(row)
    write_parquet(rows, phase_output(run_id, "p5_parse", "facts.parquet"), FACT_ARROW_SCHEMA)


def _write_inferences(entity_ids: list[str], park_class: str) -> None:
    rows = [
        {
            "inference_id": f"i:{i}",
            "domain_id": f"d:{entity_id}",
            "entity_id": entity_id,
            "run_id": RUN,
            "measured_month": MONTH,
            "category": "mail_platform",
            "vendor": "Microsoft",
            "confidence": "high",
            "park_class": park_class,
        }
        for i, entity_id in enumerate(entity_ids)
    ]
    write_parquet(
        rows,
        phase_output(RUN, "p6_infer", "inferences.parquet"),
        INFERENCE_ARROW_SCHEMA,
    )


def _gold(name: str):
    frame = read_parquet(gold_dir("2026-08") / name)
    assert frame is not None
    return frame


def test_p7_requires_facts():
    with pytest.raises(MissingInputError, match="p5-parse"):
        run_p7(run_id=RUN)


def test_p7_requires_entities():
    _write_facts(["jp:1"])
    with pytest.raises(MissingInputError, match="p1-population"):
        run_p7(run_id=RUN)


def test_p7_writes_gold():
    ids = _write_entities({"1": 8, "2": 6})
    _write_facts(ids)
    _write_inferences(ids, ParkClass.ACTIVE_SENDING)

    result = run_p7(run_id=RUN)
    assert result["status"] == "success"

    overall = _gold("stats_overall.parquet")
    assert len(overall) == 1
    row = overall.iloc[0]
    assert row["population_id"] == POP
    assert row["total_entities"] == 14
    assert row["total_domains"] == 14
    assert row["observed_domains"] == 14
    assert row["enforced_reject_domains"] == 14
    assert row["sending_domains"] == 14

    sectors = _gold("stats_by_sector.parquet")
    assert set(sectors["common12_code"]) == {"1", "2"}
    assert not sectors["suppressed"].any()


def test_p7_suppresses_small_sectors_and_avoids_recovery():
    """秘匿セルが1つになるなら2つ目を巻き込む（受け入れ基準）。"""
    ids = _write_entities({"1": 30, "2": 8, "3": 2})
    _write_facts(ids)

    result = run_p7(run_id=RUN)
    sectors = _gold("stats_by_sector.parquet")
    by_code = dict(zip(sectors["common12_code"], sectors["suppressed"], strict=True))

    # 3 だけでは合計から逆算できるので 2 も巻き込む
    assert set(by_code) == {"1", SUPPRESSED_SECTOR_CODE}
    assert bool(by_code[SUPPRESSED_SECTOR_CODE]) is True
    merged = sectors[sectors["common12_code"] == SUPPRESSED_SECTOR_CODE].iloc[0]
    assert merged["n_entities"] == 10
    assert result["breakdown"]["suppressed_sectors"] == 1
    assert any(w["code"] == "CELL_SUPPRESSED" for w in result["warnings"])
    # 逆算可能な状態のまま出していない
    assert not any(w["code"] == "SUPPRESSION_RECOVERABLE" for w in result["warnings"])


def test_p7_excludes_delisted_entities():
    """「消えた会社」を現況の分母に入れない。"""
    ids = _write_entities({"1": 6})
    frame = read_parquet(phase_output(RUN, "p1_population", "entities.parquet"))
    frame.loc[0, "status"] = EntityStatus.DELISTED
    write_parquet(
        frame, phase_output(RUN, "p1_population", "entities.parquet"), ENTITY_ARROW_SCHEMA
    )
    _write_facts(ids)

    run_p7(run_id=RUN)
    assert _gold("stats_overall.parquet").iloc[0]["total_entities"] == 5


def test_p7_warns_when_a_domain_has_no_entity():
    _write_entities({"1": 6})
    _write_facts(["jp:ghost"])
    result = run_p7(run_id=RUN)
    assert any(w["code"] == "DOMAIN_WITHOUT_ENTITY" for w in result["warnings"])
    assert _gold("stats_overall.parquet").iloc[0]["total_domains"] == 0


def test_p7_records_the_previous_month_delta():
    ids = _write_entities({"1": 6})
    _write_entities({"1": 6}, run_id=PREV)
    _write_facts(
        ids, run_id=PREV, effective_7489="none", dmarc_p="none", policy_label=PolicyLabel.MONITORING
    )
    _write_facts(ids)

    result = run_p7(run_id=RUN)
    d = json.loads(_gold("stats_overall.parquet").iloc[0]["delta_prev_month"])
    assert d["policy_upgraded"] == 6
    assert d["skipped"] is False
    assert result["breakdown"]["delta_prev_month"][POP]["policy_upgraded"] == 6


def test_p7_does_not_report_unobserved_domains_as_gone():
    ids = _write_entities({"1": 6})
    _write_entities({"1": 6}, run_id=PREV)
    _write_facts(ids, run_id=PREV)
    _write_facts(
        ids,
        observed=False,
        record_present=None,
        spf_present=None,
        dmarc_present=None,
        effective_7489=None,
        dmarc_p=None,
        policy_label=None,
    )

    result = run_p7(run_id=RUN)
    d = json.loads(_gold("stats_overall.parquet").iloc[0]["delta_prev_month"])
    assert d["domains_disappeared"] == 0
    assert d["domains_unobserved_this_month"] == 6
    assert any(w["code"] == "DOMAINS_UNOBSERVED" for w in result["warnings"])
    # 分母から外れるので採用率が 0% になるのではなく、分母自体が 0 になる
    assert _gold("stats_overall.parquet").iloc[0]["observed_domains"] == 0


def test_p7_warns_without_inferences():
    ids = _write_entities({"1": 6})
    _write_facts(ids)
    result = run_p7(run_id=RUN)
    assert any(w["code"] == "NO_INFERENCES" for w in result["warnings"])
    assert _gold("stats_overall.parquet").iloc[0]["parked_domains"] == 0


def test_p7_does_not_leak_market_segment_into_gold():
    """market_segment は内部の集計軸専用。公開成果物には出さない。"""
    ids = _write_entities({"1": 6})
    _write_facts(ids)
    run_p7(run_id=RUN)
    for name in ("stats_overall.parquet", "stats_by_sector.parquet"):
        assert "market_segment" not in _gold(name).columns


def test_p7_is_idempotent():
    ids = _write_entities({"1": 6})
    _write_facts(ids)
    run_p7(run_id=RUN)
    path = gold_dir("2026-08") / "stats_overall.parquet"
    first = path.read_bytes()
    run_p7(run_id=RUN)
    assert path.read_bytes() == first


def test_p7_skips_entities_without_common12():
    ids = _write_entities({"1": 6})
    frame = read_parquet(phase_output(RUN, "p1_population", "entities.parquet"))
    frame.loc[0, "common12_code"] = None
    write_parquet(
        frame, phase_output(RUN, "p1_population", "entities.parquet"), ENTITY_ARROW_SCHEMA
    )
    _write_facts(ids)

    result = run_p7(run_id=RUN)
    assert any(w["code"] == "COMMON12_UNMAPPED" for w in result["warnings"])
    # 全社統計の分母は減らない。業種別からだけ外れる
    assert _gold("stats_overall.parquet").iloc[0]["total_entities"] == 6


# --------------------------------------------------------------------------
# 同じ月に複数の母集団を置く
# --------------------------------------------------------------------------


def _gold_row(population_id: str, month: str = "2026-09", observed: int = 100) -> dict:
    """スキーマの必須列を埋めた最小の stats_overall 行。"""
    import datetime as dt

    from mailauth.contracts import STATS_OVERALL_ARROW_SCHEMA

    row: dict = {}
    for field in STATS_OVERALL_ARROW_SCHEMA:
        row[field.name] = None
    row["population_id"] = population_id
    row["measured_month"] = dt.date.fromisoformat(f"{month}-01")
    row["observed_domains"] = observed
    row["total_entities"] = observed
    return row


def test_同じ月の他の母集団を消さない(tmp_path):
    """gold のパスは母集団を含まないが、行は population_id を持ち、
    P8 は全母集団を縦に積んで出す。**月に複数の母集団を置ける構造なのに、
    書き込みがファイルを丸ごと置き換えていた。**

    国内を回すと同じ月の米国が理由も残さず消えていた。
    """
    from mailauth.contracts import STATS_OVERALL_ARROW_SCHEMA, STATS_OVERALL_SORT_KEYS
    from mailauth.io import write_parquet
    from mailauth.manifest import RunManifest
    from mailauth.p7_aggregate.runner import _merge_other_populations

    path = tmp_path / "stats_overall.parquet"
    write_parquet(
        [_gold_row("us-all-listed", observed=1553)],
        path,
        STATS_OVERALL_ARROW_SCHEMA,
        sort_keys=STATS_OVERALL_SORT_KEYS,
    )

    manifest = RunManifest(phase="p7_aggregate", run_id="2026-09", out_dir=tmp_path)
    merged = _merge_other_populations(
        [_gold_row("jp-all-listed", observed=3400)],
        path,
        manifest,
        what="stats_overall",
    )

    ids = sorted(str(r["population_id"]) for r in merged)
    assert ids == ["jp-all-listed", "us-all-listed"]
    assert any(w["code"] == "GOLD_OTHER_POPULATIONS_KEPT" for w in manifest.to_dict()["warnings"])


def test_同じ母集団を回し直すと自分の行だけ差し替わる(tmp_path):
    """冪等であること（原則6）。行が二重に積み上がってはいけない。"""
    from mailauth.contracts import STATS_OVERALL_ARROW_SCHEMA, STATS_OVERALL_SORT_KEYS
    from mailauth.io import write_parquet
    from mailauth.manifest import RunManifest
    from mailauth.p7_aggregate.runner import _merge_other_populations

    path = tmp_path / "stats_overall.parquet"
    write_parquet(
        [_gold_row("jp-all-listed", observed=1)],
        path,
        STATS_OVERALL_ARROW_SCHEMA,
        sort_keys=STATS_OVERALL_SORT_KEYS,
    )

    merged = _merge_other_populations(
        [_gold_row("jp-all-listed", observed=3400)],
        path,
        RunManifest(phase="p7_aggregate", run_id="2026-09", out_dir=tmp_path),
        what="stats_overall",
    )
    assert len(merged) == 1
    assert merged[0]["observed_domains"] == 3400


def test_既存ファイルが無ければそのまま書く(tmp_path):
    from mailauth.manifest import RunManifest
    from mailauth.p7_aggregate.runner import _merge_other_populations

    rows = [_gold_row("jp-all-listed")]
    merged = _merge_other_populations(
        rows,
        tmp_path / "does-not-exist.parquet",
        RunManifest(phase="p7_aggregate", run_id="2026-09", out_dir=tmp_path),
        what="stats_overall",
    )
    assert merged == rows
