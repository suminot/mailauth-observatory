"""bronze / silver の R2 退避（Sprint 8「自動化」）。

bronze を失うと過去の再解釈ができなくなり、原則1（生データは不変）の意味が
なくなる。検証の主眼は「送ったつもり」で終わらないこと。
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from mailauth.cli import app
from mailauth.offload import OFFLOAD_DIRS, missing_settings, offload, plan
from mailauth.paths import run_dir

RUN = "2026-08"
runner = CliRunner()


@pytest.fixture
def sample_run():
    root = run_dir(RUN)
    (root / "p4_measure" / "bronze" / "method=dnspython").mkdir(parents=True)
    (root / "p4_measure" / "bronze" / "method=dnspython" / "part-0000.jsonl.zst").write_bytes(
        b"x" * 100
    )
    (root / "p4_measure" / "_manifest.json").write_text("{}", encoding="utf-8")
    (root / "p5_parse").mkdir(parents=True)
    (root / "p5_parse" / "facts.parquet").write_bytes(b"y" * 50)
    # gold は Git にコミットするので退避対象ではない
    (root / "p7_aggregate").mkdir(parents=True)
    (root / "p7_aggregate" / "_manifest.json").write_text("{}", encoding="utf-8")
    return root


class _FakeS3:
    def __init__(self, fail_on: str | None = None):
        self.uploaded: list[tuple[str, str]] = []
        self._fail_on = fail_on

    def upload_file(self, path, bucket, key):
        if self._fail_on and self._fail_on in key:
            raise RuntimeError("接続できない")
        self.uploaded.append((bucket, key))


def _configure(monkeypatch):
    for name, value in (
        ("R2_ENDPOINT", "https://r2.example"),
        ("R2_BUCKET", "mailauth"),
        ("R2_ACCESS_KEY_ID", "key"),
        ("R2_SECRET_ACCESS_KEY", "secret"),
    ):
        monkeypatch.setenv(name, value)


def test_plan_counts_before_sending(sample_run):
    """送る前に件数を確定させる。落ちたときに分母付きで言えるようにする。"""
    p = plan(RUN)
    assert p.count == 3
    assert p.total_bytes == 152  # bronze 100 + facts 50 + manifest "{}" 2
    # gold は Git にコミットするので対象外
    assert "p7_aggregate" not in OFFLOAD_DIRS


def test_plan_records_missing_directories(sample_run):
    p = plan(RUN)
    assert "p6_infer" in p.skipped_dirs


def test_missing_credentials_do_not_pretend_to_succeed(sample_run, monkeypatch):
    """黙って成功したふりをすると、気付くのはリポジトリを失ったあとになる。"""
    for name in ("R2_ENDPOINT", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert len(missing_settings()) == 4

    p, result = offload(RUN)
    assert result.skipped is True
    assert result.uploaded == 0
    assert "設定が足りない" in (result.reason or "")
    # 対象の件数自体は分かる
    assert p.count == 3


def test_dry_run_sends_nothing(sample_run, monkeypatch):
    _configure(monkeypatch)
    _, result = offload(RUN, dry_run=True)
    assert result.skipped is True
    assert result.uploaded == 0
    assert "dry_run" in (result.reason or "")


def test_offload_uploads_with_a_run_scoped_prefix(sample_run, monkeypatch):
    _configure(monkeypatch)
    s3 = _FakeS3()
    _, result = offload(RUN, client=s3)

    assert result.uploaded == 3
    assert result.failed == 0
    keys = sorted(k for _, k in s3.uploaded)
    assert keys[0].startswith(f"runs/{RUN}/")
    assert any("bronze/method=dnspython" in k for k in keys)
    assert result.uploaded_bytes == 152


def test_a_single_failure_does_not_stop_the_rest(sample_run, monkeypatch):
    """1件の失敗で全体を止めない。件数と理由は残す（原則4）。"""
    _configure(monkeypatch)
    s3 = _FakeS3(fail_on="facts.parquet")
    _, result = offload(RUN, client=s3)

    assert result.uploaded == 2
    assert result.failed == 1
    assert result.errors
    assert "再実行" in (result.reason or "")


def test_cli_reports_the_skip_reason(sample_run, monkeypatch):
    for name in ("R2_ENDPOINT", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(name, raising=False)
    result = runner.invoke(app, ["offload", "--run", RUN])
    assert result.exit_code == 0
    assert "設定が足りない" in result.output


def test_cli_exits_nonzero_when_uploads_fail(sample_run, monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(
        "mailauth.offload._client", lambda cfg: _FakeS3(fail_on="part-0000")
    )
    result = runner.invoke(app, ["offload", "--run", RUN])
    assert result.exit_code == 1
