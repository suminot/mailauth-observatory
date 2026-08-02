"""P1 母集団確定の受け入れ検証。"""

from __future__ import annotations

import pandas as pd
import pytest

from mailauth.io import read_parquet
from mailauth.manifest import read_manifest
from mailauth.p1_population import PopulationNotImplementedError, run
from mailauth.paths import phase_dir, phase_output


def run_p1(run_id: str, source, config="configs/populations/jp-all-listed.yaml", **kw):
    return run(config=config, run_id=run_id, source_file=source, **kw)


def entities(run_id: str) -> pd.DataFrame:
    df = read_parquet(phase_output(run_id, "p1_population", "entities.parquet"))
    assert df is not None
    return df


# -- 基本 -------------------------------------------------------------------


def test_produces_entities_and_manifest(edinet_sample):
    result = run_p1("2026-08", edinet_sample)
    assert result["status"] == "success"
    assert read_manifest(phase_dir("2026-08", "p1_population")) is not None

    df = entities("2026-08")
    # 20行のうち 上場17社（非上場2件は除外、重複1件は統合）
    assert len(df) == 17
    assert result["counts"]["input"] == 20
    assert result["counts"]["success"] == 17


def test_only_listed_companies_are_kept(edinet_sample):
    """証券コードが空のレコードは上場企業ではない（market_filter の方法）。"""
    run_p1("2026-08", edinet_sample)
    df = entities("2026-08")
    assert df["securities_code"].notna().all()
    assert "サンプル非上場株式会社" not in set(df["name"])


def test_securities_code_is_normalized_to_four_digits(edinet_sample):
    run_p1("2026-08", edinet_sample)
    df = entities("2026-08")
    assert all(len(c) == 4 for c in df["securities_code"])


def test_entity_id_scheme_supports_future_lei_migration(edinet_sample):
    """entity_id は接頭辞付き。Sprint 1.5 で lei: へ移行できる形にしてある。"""
    run_p1("2026-08", edinet_sample)
    df = entities("2026-08")
    assert all(e.startswith(("jp:", "edinet:")) for e in df["entity_id"])
    # 法人番号があれば jp:、無ければ edinet: にフォールバック
    assert "jp:1234567890123" in set(df["entity_id"])
    assert "edinet:E00018" in set(df["entity_id"])


def test_population_ids_is_always_an_array(edinet_sample):
    """Sprint 1.5 で global500 と重複排除するため、最初から配列で持つ。"""
    run_p1("2026-08", edinet_sample)
    df = entities("2026-08")
    assert all(list(v) == ["jp-all-listed"] for v in df["population_ids"])


def test_duplicate_houjin_bangou_is_merged(edinet_sample):
    """同じ法人番号で EDINETコードが2つある提出者は1社に統合する。"""
    run_p1("2026-08", edinet_sample)
    df = entities("2026-08")
    assert (df["entity_id"] == "jp:1234567890123").sum() == 1


def test_industry_is_mapped_to_common12(edinet_sample):
    run_p1("2026-08", edinet_sample)
    df = entities("2026-08").set_index("entity_id")
    assert df.loc["jp:2234567890123", "industry_label"] == "輸送用機器"
    assert df.loc["jp:2234567890123", "common12_code"] == "5"
    assert df.loc["jp:2234567890123", "industry_scheme"] == "EDINET33"
    assert df["industry_map_version"].notna().all()


def test_unmapped_industry_is_reported_not_fatal(edinet_sample):
    result = run_p1("2026-08", edinet_sample)
    codes = [w["code"] for w in result["warnings"]]
    assert "INDUSTRY_UNMAPPED" in codes
    assert result["status"] == "success"  # 落とさない
    df = entities("2026-08").set_index("entity_id")
    assert pd.isna(df.loc["jp:1934567890123", "common12_code"])


# -- 原則6 冪等 --------------------------------------------------------------


def test_running_twice_produces_identical_output(edinet_sample):
    run_p1("2026-08", edinet_sample)
    path = phase_output("2026-08", "p1_population", "entities.parquet")
    first = path.read_bytes()
    run_p1("2026-08", edinet_sample)
    assert path.read_bytes() == first


# -- 前月差分 ---------------------------------------------------------------


def test_first_run_has_no_diff(edinet_sample):
    result = run_p1("2026-08", edinet_sample)
    assert result["breakdown"]["diff"]["previous_available"] is False
    assert "NO_PREVIOUS_RUN" in {w["code"] for w in result["warnings"]}
    df = entities("2026-08")
    assert (df["first_seen_month"] == df["last_seen_month"]).all()


def test_detects_new_renamed_and_delisted(edinet_sample, tmp_path):
    run_p1("2026-08", edinet_sample)

    text = edinet_sample.read_bytes().decode("cp932")
    text = text.replace("株式会社サンプル銀行", "株式会社サンプルフィナンシャルグループ")
    lines = [ln for ln in text.split("\r\n") if not ln.startswith("E00009")]  # 海運が消滅
    lines.insert(
        -1,
        "E00030,内国法人,上場,有,9000000,3月31日,株式会社サンプル新規上場,"
        "Sample Newly Listed Inc.,サンプルシンキジョウジョウ,東京都中野区,情報・通信業,"
        "30010,3034567890123",
    )
    september = tmp_path / "sep.csv"
    september.write_bytes("\r\n".join(lines).encode("cp932"))

    result = run_p1("2026-09", september)
    diff = result["breakdown"]["diff"]
    assert diff["previous_available"] is True
    assert diff["new_entities"] == 1
    assert diff["renamed_entities"] == 1
    assert diff["delisted_entities"] == 1

    df = entities("2026-09").set_index("entity_id")
    renamed = df.loc["jp:3234567890123"]
    assert renamed["status"] == "renamed"
    assert "社名変更" in renamed["change_note"]
    # 消滅した企業は last_seen_month を前月のまま残す（「消えた」と「取れなかった」を分ける）
    delisted = df.loc["jp:9234567890123"]
    assert delisted["status"] == "delisted"
    assert str(delisted["last_seen_month"]) == "2026-08-01"
    # 継続企業は first_seen_month が前月のまま引き継がれる
    assert str(df.loc["jp:1234567890123", "first_seen_month"]) == "2026-08-01"


def test_limit_skips_diff_to_avoid_false_delisting(edinet_sample):
    """--limit で母集団を切り詰めた状態で差分を取ると全社が delisted になる。取らない。"""
    run_p1("2026-08", edinet_sample)
    result = run_p1("2026-09", edinet_sample, limit=3)
    assert "DIFF_SKIPPED_DUE_TO_LIMIT" in {w["code"] for w in result["warnings"]}
    assert "delisted" not in set(entities("2026-09")["status"])


# -- 受け入れ基準の判定 -------------------------------------------------------


def test_acceptance_is_reported_not_enforced(edinet_sample):
    """基準未達は観測結果である。実行は止めず manifest に残す。"""
    result = run_p1("2026-08", edinet_sample)
    codes = {w["code"] for w in result["warnings"]}
    assert "ACCEPTANCE_COUNT_OUT_OF_RANGE" in codes  # 17件は想定3,500件超の外
    assert result["status"] == "success"
    assert result["breakdown"]["acceptance"]["count_in_range"] is False


def test_missing_credentials_are_recorded_not_silent(edinet_sample):
    """認証情報が無くて取れなかったことを、黙って欠損にしない（原則5の精神）。"""
    result = run_p1("2026-08", edinet_sample)
    codes = {w["code"] for w in result["warnings"]}
    assert "ENRICH_SKIPPED_GBIZINFO" in codes
    assert "ENRICH_SKIPPED_HOUJIN_BANGOU" in codes


def test_attribution_is_carried_into_manifest(edinet_sample):
    """出典表記は成果物に含める（公共データ利用規約1.0 / 政府標準利用規約2.0）。"""
    result = run_p1("2026-08", edinet_sample)
    assert any("EDINET" in a for a in result["attribution"])


# -- 実行モード -------------------------------------------------------------


def test_dry_run_writes_no_output(edinet_sample):
    result = run_p1("2026-08", edinet_sample, dry_run=True)
    assert result["outputs"] == []
    assert "DRY_RUN" in {w["code"] for w in result["warnings"]}
    assert not phase_output("2026-08", "p1_population", "entities.parquet").exists()
    assert read_manifest(phase_dir("2026-08", "p1_population")) is not None


def test_limit_caps_the_number_of_entities(edinet_sample):
    run_p1("2026-08", edinet_sample, limit=3)
    assert len(entities("2026-08")) == 3


# -- 未実装の母集団 ----------------------------------------------------------


@pytest.mark.parametrize(
    "config",
    [
        "configs/populations/us-fortune500.yaml",  # Sprint 1.5
        "configs/populations/global500.yaml",  # Sprint 1.5
        "configs/populations/jp-standard.yaml",  # 市場区分ソースが未確保
    ],
)
def test_unimplemented_populations_fail_loudly(config, edinet_sample):
    """未実装のものは空の結果を出さず、理由を添えて止まる。"""
    with pytest.raises(PopulationNotImplementedError):
        run_p1("2026-08", edinet_sample, config=config)
    # 止まっても manifest は残す
    manifest = read_manifest(phase_dir("2026-08", "p1_population"))
    assert manifest["status"] == "failed"


def test_jp_prime_warns_that_segment_is_unavailable(edinet_sample):
    """segment_source が none のままでは市場区分で絞れないことを明示する。"""
    result = run_p1("2026-08", edinet_sample, config="configs/populations/jp-prime.yaml")
    assert "MARKET_SEGMENT_UNAVAILABLE" in {w["code"] for w in result["warnings"]}


def test_segment_allowlist_filters_by_securities_code(edinet_sample, tmp_path, monkeypatch):
    """JPX 由来でない証券コード一覧を与えれば区分で絞れる。"""
    import yaml

    from mailauth import paths

    allowlist = tmp_path / "codes.csv"
    allowlist.write_text("1234\n2345\n", encoding="utf-8")

    base = yaml.safe_load(
        (paths.repo_root() / "configs/populations/jp-all-listed.yaml").read_text(encoding="utf-8")
    )
    base["source"]["market_filter"]["segment_allowlist"] = str(allowlist)
    cfg = tmp_path / "custom.yaml"
    cfg.write_text(yaml.safe_dump(base, allow_unicode=True), encoding="utf-8")

    run_p1("2026-08", edinet_sample, config=cfg)
    df = entities("2026-08")
    assert set(df["securities_code"]) == {"1234", "2345"}
