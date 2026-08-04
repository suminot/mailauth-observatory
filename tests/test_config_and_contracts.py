"""設定の読み込みとデータ契約の整合。"""

from __future__ import annotations

import datetime as dt

import pytest
import yaml

from mailauth import PHASE_LABELS, PHASE_OUTPUTS, PHASES
from mailauth.config import (
    list_populations,
    load_measure_config,
    load_population,
    load_selector_list,
    load_yaml,
)
from mailauth.contracts import (
    ENTITY_ARROW_SCHEMA,
    MIN_CELL_SIZE,
    Confidence,
    Entity,
    MeasureTier,
    ParkClass,
)
from mailauth.io import records_to_frame, write_parquet
from mailauth.paths import (
    default_run_id,
    month_date,
    previous_run_id,
    repo_root,
    validate_run_id,
)

# -- 設定 -------------------------------------------------------------------


def test_every_population_yaml_is_valid():
    populations = list_populations()
    assert len(populations) >= 5
    assert {p.id for p in populations} >= {"jp-prime", "us-fortune500", "global500"}


#: source.primary ごとに必須の設定
_REQUIRED_BY_PRIMARY = {
    "edinet_code_list": ("edinet_code_list",),
    "sec_edgar": (),
    "wikidata": ("wikidata_query",),
}


def test_implemented_populations_have_the_settings_they_need():
    for cfg in list_populations():
        if not (cfg.implemented and cfg.enabled):
            continue
        primary = cfg.source.primary
        assert primary in _REQUIRED_BY_PRIMARY, f"{cfg.id}: 未知の primary {primary}"
        for field_name in _REQUIRED_BY_PRIMARY[primary]:
            assert getattr(cfg.source, field_name, None) is not None, (
                f"{cfg.id} に source.{field_name} がない"
            )
        # 業種軸で集計するので写像は実装済みの母集団すべてに要る
        assert cfg.source.industry.common_mapping, cfg.id
        assert (repo_root() / cfg.source.industry.common_mapping).is_file(), cfg.id


def test_unimplemented_populations_state_why():
    for cfg in list_populations():
        if not cfg.implemented:
            assert cfg.blocked_by, f"{cfg.id} に blocked_by がない"


def test_measure_config_declares_both_tiers():
    """階層別計測（A: フル / C: 簡易）は6時間上限への対策なので必須。"""
    tiers = load_measure_config()["tiers"]
    assert set(tiers) == {"A", "C"}
    assert "dkim" in tiers["A"]["queries"]
    assert "dkim" not in tiers["C"]["queries"]  # 階層Cにフル計測はかけない


def test_measure_config_keeps_dane_and_extras():
    extras = load_measure_config()["extras"]
    assert extras["dane"] is True  # 欧州比較軸として保持する
    assert extras["mta_sts"] and extras["tls_rpt"] and extras["bimi"] and extras["dnssec"]


def test_l1_selector_list_is_in_the_expected_size_range():
    """L1 は40〜60。Wang et al. の頻出上位40相当 + 主要ESP既定。"""
    selectors = load_selector_list()
    assert 40 <= len(selectors) <= 60
    assert len(selectors) == len(set(selectors))
    assert {"selector1", "selector2", "google", "default"} <= set(selectors)


@pytest.mark.parametrize(
    "path",
    [
        "configs/fingerprints/platforms.yaml",
        "configs/fingerprints/security_gw.yaml",
        "configs/fingerprints/esp.yaml",
        "configs/fingerprints/verification_txt.yaml",
        "configs/vendors/dmarc_rua_vendors.yaml",
    ],
)
def test_fingerprint_rules_are_well_formed(path):
    import re

    data = load_yaml(path)
    assert data.get("version")
    for rule in data.get("rules", []):
        assert rule["id"] and rule["vendor"] and rule["confidence"]
        assert rule["match"]["record"] and rule["match"]["pattern"]
        re.compile(rule["match"]["pattern"])  # 正規表現として妥当か


def test_fingerprint_rule_ids_are_unique_across_files():
    seen: dict[str, str] = {}
    for path in (
        "configs/fingerprints/platforms.yaml",
        "configs/fingerprints/security_gw.yaml",
        "configs/fingerprints/esp.yaml",
        "configs/fingerprints/verification_txt.yaml",
        "configs/vendors/dmarc_rua_vendors.yaml",
    ):
        for rule in load_yaml(path).get("rules", []):
            assert rule["id"] not in seen, f"rule_id が重複: {rule['id']}"
            seen[rule["id"]] = path


def test_japanese_vendor_rules_exist():
    """国内ベンダー辞書は本システムの差別化点。空にしない。"""
    jp = [r for r in load_yaml("configs/fingerprints/security_gw.yaml")["rules"]
          if r.get("region") == "JP"]
    assert len(jp) >= 4


def test_common12_definition_matches_the_mapping():
    classes = load_yaml("configs/industry/common12.yaml")["classes"]
    assert [c["code"] for c in classes] == [str(i) for i in range(1, 13)]
    assert load_yaml("configs/industry/common12.yaml")["aggregation"]["min_cell_size"] == (
        MIN_CELL_SIZE
    )


def test_population_yaml_files_have_no_tabs():
    """YAML はタブを許さない。エディタ設定の事故を早く見つける。"""
    for path in (repo_root() / "configs").rglob("*.yaml"):
        assert "\t" not in path.read_text(encoding="utf-8"), path


def test_missing_population_config_raises():
    with pytest.raises(FileNotFoundError):
        load_population("configs/populations/does-not-exist.yaml")


# -- パス規約 ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("run_id", "expected"),
    [("2026-08", "2026-07"), ("2026-01", "2025-12"), ("2026-03-rerun", "2026-02")],
)
def test_previous_run_id(run_id, expected):
    assert previous_run_id(run_id) == expected


def test_month_date():
    assert month_date("2026-08") == dt.date(2026, 8, 1)


def test_default_run_id_is_current_month():
    assert validate_run_id(default_run_id()) == default_run_id()


@pytest.mark.parametrize("bad", ["2026", "26-08", "2026/08", "", "abc"])
def test_invalid_run_id_is_rejected(bad):
    with pytest.raises(ValueError):
        validate_run_id(bad)


# -- 契約 -------------------------------------------------------------------


def test_all_phases_have_labels_and_outputs():
    assert set(PHASES) == set(PHASE_LABELS) == set(PHASE_OUTPUTS)
    assert len(PHASES) == 8


def test_entity_model_and_arrow_schema_agree():
    """Pydantic と PyArrow がずれると、書けるが読めない Parquet ができる。"""
    assert set(Entity.model_fields) == set(ENTITY_ARROW_SCHEMA.names)


def test_confidence_and_tier_vocabularies():
    assert {c.value for c in Confidence} == {"confirmed", "likely", "unknown", "parked"}
    assert {t.value for t in MeasureTier} == {"A", "C"}
    # パークドメイン分類は本システム固有の指標。6段階すべてを持つ
    assert len(ParkClass) == 6


def test_empty_frame_keeps_the_declared_schema(tmp_path):
    """全件 NULL の月でも型が揺れないこと。揺れると月次結合が壊れる。"""
    import pyarrow.parquet as pq

    path = tmp_path / "empty.parquet"
    assert write_parquet([], path, ENTITY_ARROW_SCHEMA) == 0
    assert pq.read_schema(path).names == ENTITY_ARROW_SCHEMA.names


def test_records_to_frame_rejects_unknown_columns():
    with pytest.raises(ValueError, match="スキーマに無い列"):
        records_to_frame([{"entity_id": "x", "bogus": 1}], ENTITY_ARROW_SCHEMA)


def test_write_parquet_sorts_for_determinism(tmp_path):
    rows = [
        {"entity_id": "jp:2", "run_id": "2026-08", "country": "JP", "name": "b",
         "name_normalized": "b", "status": "active", "population_ids": []},
        {"entity_id": "jp:1", "run_id": "2026-08", "country": "JP", "name": "a",
         "name_normalized": "a", "status": "active", "population_ids": []},
    ]
    import pandas as pd

    path = tmp_path / "e.parquet"
    write_parquet(rows, path, ENTITY_ARROW_SCHEMA, sort_keys=["entity_id"])
    assert list(pd.read_parquet(path)["entity_id"]) == ["jp:1", "jp:2"]


def test_design_document_is_committed():
    """設計仕様がリポジトリに無いと、引き継ぎ時に何も分からない。"""
    design = repo_root() / "DESIGN.md"
    assert design.is_file()
    assert "メール認証月次計測システム" in design.read_text(encoding="utf-8")[:200]


def test_sparql_queries_exist_for_sprint_1_5():
    for name in ("fortune500.rq", "global500.rq"):
        path = repo_root() / "configs/populations/_sparql" / name
        assert path.is_file()
        assert "SELECT" in path.read_text(encoding="utf-8")


def test_yaml_configs_all_parse():
    for path in (repo_root() / "configs").rglob("*.yaml"):
        yaml.safe_load(path.read_text(encoding="utf-8"))
