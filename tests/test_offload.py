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


def _configure_backup(monkeypatch):
    for name, value in (
        ("BACKUP_ENDPOINT", "https://backup.example"),
        ("BACKUP_BUCKET", "mailauth-backup"),
        ("BACKUP_ACCESS_KEY_ID", "key2"),
        ("BACKUP_SECRET_ACCESS_KEY", "secret2"),
    ):
        monkeypatch.setenv(name, value)


def _clear_all(monkeypatch):
    from mailauth.offload import DESTINATION_ENV

    for env in DESTINATION_ENV.values():
        for name in env:
            monkeypatch.delenv(name, raising=False)


def test_a_single_destination_is_not_called_durable(sample_run, monkeypatch):
    """**送れたことと持続性の基準を満たしたことは別である**（DESIGN.md 第9章）。"""
    _clear_all(monkeypatch)
    _configure(monkeypatch)
    _, result = offload(RUN, clients={"r2": _FakeS3()})
    assert result.uploaded == 3
    assert result.failed == 0
    assert result.single_destination is True
    assert any("持続性の基準は満たしていない" in n for n in result.notes)
    assert result.destinations_used == ["r2"]


def test_two_destinations_each_receive_every_file(sample_run, monkeypatch):
    """R2 以外の保管先にも同じものを送る。片方だけでは基準を満たさない。"""
    _clear_all(monkeypatch)
    _configure(monkeypatch)
    _configure_backup(monkeypatch)
    primary, secondary = _FakeS3(), _FakeS3()
    _, result = offload(RUN, clients={"r2": primary, "backup": secondary})

    assert result.single_destination is False
    assert result.notes == []
    assert result.destinations_used == ["backup", "r2"]
    assert len(primary.uploaded) == 3
    assert len(secondary.uploaded) == 3
    # **別のバケットに入っていること。** 同じ所に2回送っても意味がない
    assert {b for b, _ in primary.uploaded} == {"mailauth"}
    assert {b for b, _ in secondary.uploaded} == {"mailauth-backup"}
    assert result.uploaded == 6


def test_a_failure_on_one_destination_does_not_stop_the_other(sample_run, monkeypatch):
    """**片方が落ちてももう片方は送る。** どちらを再実行すべきか分かる形で残す。"""
    _clear_all(monkeypatch)
    _configure(monkeypatch)
    _configure_backup(monkeypatch)
    _, result = offload(
        RUN,
        clients={"r2": _FakeS3(fail_on="facts.parquet"), "backup": _FakeS3()},
    )
    assert result.by_destination["r2"].failed == 1
    assert result.by_destination["r2"].uploaded == 2
    assert result.by_destination["backup"].failed == 0
    assert result.by_destination["backup"].uploaded == 3
    # 再実行すべき宛先が名前で分かる
    assert "r2" in (result.reason or "")
    assert all(e.startswith("[r2]") for e in result.errors)


def test_only_the_backup_configured_still_works(sample_run, monkeypatch):
    """R2 が無くても副だけで送れる。**R2 を特別扱いしない。**"""
    _clear_all(monkeypatch)
    _configure_backup(monkeypatch)
    _, result = offload(RUN, clients={"backup": _FakeS3()})
    assert result.uploaded == 3
    assert result.destinations_used == ["backup"]
    assert result.single_destination is True


def test_no_destination_configured_reports_every_missing_key(sample_run, monkeypatch):
    """どちらの宛先の何が足りないかを言う。片方だけ挙げると設定が進まない。"""
    _clear_all(monkeypatch)
    _, result = offload(RUN)
    assert result.skipped is True
    assert result.uploaded == 0
    assert "r2=" in (result.reason or "")
    assert "backup=" in (result.reason or "")


def test_dry_run_counts_every_destination(sample_run, monkeypatch):
    _clear_all(monkeypatch)
    _configure(monkeypatch)
    _configure_backup(monkeypatch)
    _, result = offload(RUN, dry_run=True)
    assert result.skipped is True
    assert result.uploaded == 0
    assert "2 宛先" in (result.reason or "")


def test_the_result_dict_carries_the_durability_state(sample_run, monkeypatch):
    _clear_all(monkeypatch)
    _configure(monkeypatch)
    payload = offload(RUN, clients={"r2": _FakeS3()})[1].to_dict()
    assert payload["single_destination"] is True
    assert payload["required_destinations"] == 2
    assert payload["destinations_used"] == ["r2"]
    assert "r2" in payload["by_destination"]


def test_the_second_destination_is_not_just_another_r2_bucket():
    """**同じ事業者に2つ置いても持続性は上がらない。**"""
    from mailauth.offload import DESTINATION_ENV

    assert set(DESTINATION_ENV) == {"r2", "backup"}
    assert not any(k.startswith("R2_") for k in DESTINATION_ENV["backup"])


def test_the_cli_reports_each_destination(sample_run, monkeypatch):
    _clear_all(monkeypatch)
    _configure(monkeypatch)
    monkeypatch.setattr("mailauth.offload._client", lambda d: _FakeS3())
    result = runner.invoke(app, ["offload", "--run", RUN])
    assert result.exit_code == 0
    assert "r2: 送信 3 件" in result.output
    # 送信が成功していても基準未達は言う
    assert "持続性の基準は満たしていない" in result.output
