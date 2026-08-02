"""原則4 ── 何件処理して何件失敗したかを必ず記録する。"""

from __future__ import annotations

import json

import pytest

from mailauth.manifest import (
    MANIFEST_FILENAME,
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_SUCCESS,
    RunManifest,
    config_hash,
    read_manifest,
)


def test_writes_manifest_on_success(tmp_path):
    with RunManifest(run_id="2026-08", phase="p1_population", out_dir=tmp_path) as m:
        m.counts.input = 100
        m.counts.success = 100

    data = json.loads((tmp_path / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert data["status"] == STATUS_SUCCESS
    assert data["counts"] == {"input": 100, "success": 100, "failed": 0, "skipped": 0}
    assert data["run_id"] == "2026-08"
    assert data["duration_sec"] >= 0
    assert data["tool_versions"]["mailauth"]


def test_partial_when_some_failed(tmp_path):
    with RunManifest(run_id="2026-08", phase="p4_measure", out_dir=tmp_path) as m:
        m.counts.input = 10
        m.counts.success = 8
        m.add_failure("TIMEOUT", 2)

    data = read_manifest(tmp_path)
    assert data["status"] == STATUS_PARTIAL
    assert data["counts"]["failed"] == 2
    assert data["failure_breakdown"] == {"TIMEOUT": 2}


def test_manifest_survives_exception(tmp_path):
    """例外で落ちても manifest は残る。残らないと工程が可視化できない。"""
    with pytest.raises(ValueError):
        with RunManifest(run_id="2026-08", phase="p1_population", out_dir=tmp_path) as m:
            m.counts.input = 5
            raise ValueError("わざと落とす")

    data = read_manifest(tmp_path)
    assert data["status"] == STATUS_FAILED
    assert "わざと落とす" in data["error"]
    assert data["counts"]["input"] == 5


def test_warnings_are_aggregated_with_capped_samples(tmp_path):
    with RunManifest(run_id="2026-08", phase="p2_candidates", out_dir=tmp_path) as m:
        for i in range(10):
            m.add_warning("WILDCARD_DNS", sample=[f"example{i}.co.jp"])

    warnings = read_manifest(tmp_path)["warnings"]
    assert len(warnings) == 1
    assert warnings[0]["count"] == 10
    assert len(warnings[0]["sample"]) == 5  # サンプルは5件まで


def test_add_output_records_size(tmp_path):
    target = tmp_path / "entities.parquet"
    target.write_bytes(b"x" * 42)
    with RunManifest(run_id="2026-08", phase="p1_population", out_dir=tmp_path) as m:
        m.add_output("entities.parquet", records=7)

    out = read_manifest(tmp_path)["outputs"][0]
    assert out == {"path": "entities.parquet", "records": 7, "bytes": 42}


def test_read_manifest_returns_none_when_missing(tmp_path):
    """未実行のフェーズは異常ではない。None を返して呼び出し側に判断させる。"""
    assert read_manifest(tmp_path / "nope") is None


def test_config_hash_is_stable_and_content_sensitive(tmp_path):
    a = tmp_path / "a.yaml"
    a.write_text("x: 1", encoding="utf-8")
    first = config_hash(a)
    assert first == config_hash(a)
    a.write_text("x: 2", encoding="utf-8")
    assert config_hash(a) != first
