"""bronze / silver の R2 への退避（DESIGN.md Sprint 8「自動化」）。

bronze と silver は Git に入れない。git-scraping パターンの既知の弱点である
履歴肥大化を避けるためで、ある事例では80MBのデータで4年運用後に
リポジトリが1GB超に膨張している（DESIGN.md 第4章）。

**退避は「送ったつもり」で終わらせない。** 送ったファイル数とバイト数を
manifest に残し、送れなかったものは理由付きで数える。bronze を失うと
過去の再解釈ができなくなり、原則1（生データは不変）の意味がなくなる。

R2 は S3 互換なので boto3 で話す。認証情報が無ければ**何もせずに
その旨を返す。** 黙って成功したふりをすると、退避されていないことに
気付くのはリポジトリを失ったあとになる。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .config import credential
from .paths import run_dir

#: 退避対象。gold は Git にコミットするので含めない
OFFLOAD_DIRS = ("p4_measure", "p5_parse", "p6_infer")


class OffloadError(RuntimeError):
    pass


@dataclass
class OffloadPlan:
    """何を送るか。実際に送る前に件数を確定させる。"""

    files: list[Path] = field(default_factory=list)
    total_bytes: int = 0
    skipped_dirs: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.files)


@dataclass
class OffloadResult:
    uploaded: int = 0
    uploaded_bytes: int = 0
    failed: int = 0
    skipped: bool = False
    reason: str | None = None
    bucket: str | None = None
    prefix: str | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "uploaded": self.uploaded,
            "uploaded_bytes": self.uploaded_bytes,
            "failed": self.failed,
            "skipped": self.skipped,
            "reason": self.reason,
            "bucket": self.bucket,
            "prefix": self.prefix,
            "errors": self.errors[:5],
        }


def settings() -> dict[str, str | None]:
    """R2 の接続情報。`.env` から読む。コードにも設定にも書かない。"""
    return {
        "endpoint": credential("R2_ENDPOINT"),
        "bucket": credential("R2_BUCKET"),
        "access_key": credential("R2_ACCESS_KEY_ID"),
        "secret_key": credential("R2_SECRET_ACCESS_KEY"),
    }


def missing_settings() -> list[str]:
    return [name for name, value in settings().items() if not value]


def plan(run_id: str, *, dirs: tuple[str, ...] = OFFLOAD_DIRS) -> OffloadPlan:
    """送るファイルを列挙する。

    **送る前に件数を確定させる。** 途中で落ちたときに「何件送れたか」を
    分母付きで言えるようにするため。
    """
    result = OffloadPlan()
    root = run_dir(run_id)
    for name in dirs:
        directory = root / name
        if not directory.is_dir():
            result.skipped_dirs.append(name)
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                result.files.append(path)
                result.total_bytes += path.stat().st_size
    return result


def _client(cfg: dict[str, str | None]):
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - 依存が無い環境向け
        raise OffloadError(
            "boto3 がありません。pip install 'mailauth-observatory[offload]' を実行してください"
        ) from exc

    return boto3.client(
        "s3",
        endpoint_url=cfg["endpoint"],
        aws_access_key_id=cfg["access_key"],
        aws_secret_access_key=cfg["secret_key"],
        region_name="auto",
    )


def offload(
    run_id: str,
    *,
    dry_run: bool = False,
    dirs: tuple[str, ...] = OFFLOAD_DIRS,
    client=None,
) -> tuple[OffloadPlan, OffloadResult]:
    """bronze / silver を R2 に送る。

    認証情報が無ければ**何もせずに理由を返す。** 黙って成功したふりを
    すると、退避されていないことに気付くのはリポジトリを失ったあとになる。
    """
    the_plan = plan(run_id, dirs=dirs)
    result = OffloadResult()

    cfg = settings()
    missing = missing_settings()
    if client is None and missing:
        result.skipped = True
        result.reason = (
            f"R2 の設定が足りないため退避していない: {', '.join(missing)}。"
            "bronze を失うと過去の再解釈ができなくなる"
        )
        return the_plan, result

    result.bucket = cfg["bucket"]
    result.prefix = f"runs/{run_id}"

    if dry_run:
        result.skipped = True
        result.reason = f"dry_run のため送っていない（対象 {the_plan.count} 件）"
        return the_plan, result

    s3 = client or _client(cfg)
    root = run_dir(run_id)
    for path in the_plan.files:
        key = f"{result.prefix}/{path.relative_to(root).as_posix()}"
        try:
            s3.upload_file(str(path), cfg["bucket"], key)
        except Exception as exc:  # noqa: BLE001 - 1件の失敗で全体を止めない
            result.failed += 1
            if len(result.errors) < 10:
                result.errors.append(f"{key}: {exc}")
            continue
        result.uploaded += 1
        result.uploaded_bytes += path.stat().st_size

    if result.failed:
        result.reason = (
            f"{result.failed} 件を送れなかった。bronze が欠けると"
            "その月の再解釈ができなくなるので、再実行すること"
        )
    return the_plan, result
