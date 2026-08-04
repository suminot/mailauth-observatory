"""bronze / silver の退避（DESIGN.md Sprint 8「自動化」/ 第9章「持続性」）。

bronze と silver は Git に入れない。git-scraping パターンの既知の弱点である
履歴肥大化を避けるためで、ある事例では80MBのデータで4年運用後に
リポジトリが1GB超に膨張している（DESIGN.md 第4章）。

**退避は「送ったつもり」で終わらせない。** 送ったファイル数とバイト数を
manifest に残し、送れなかったものは理由付きで数える。bronze を失うと
過去の再解釈ができなくなり、原則1（生データは不変）の意味がなくなる。

## 宛先は複数持てる

DESIGN.md 第9章は「bronze のバックアップを **R2 以外に**持つ」ことを
持続性の受け入れ基準に挙げている。1宛先しか送れない実装では、運用者が
第2の保管先を用意しようとしてもできない。**設定の問題ではなく実装の
問題になってしまう**ので、宛先を複数受け付ける。

**宛先が1つしか設定されていない状態を「成功」と呼ばない。** 送れたことと
持続性の基準を満たしたことは別である（原則5の延長）。1宛先のときは
`single_destination` を立て、理由を添える。

いずれも S3 互換なので boto3 で話す。認証情報が無ければ**何もせずに
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

#: 宛先ごとの環境変数。**同じ事業者に2つ置いても持続性は上がらない**ので、
#: 第2宛先は別系統（別事業者・別アカウント）を想定した名前にしてある
DESTINATION_ENV: dict[str, tuple[str, str, str, str]] = {
    "r2": (
        "R2_ENDPOINT",
        "R2_BUCKET",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
    ),
    "backup": (
        "BACKUP_ENDPOINT",
        "BACKUP_BUCKET",
        "BACKUP_ACCESS_KEY_ID",
        "BACKUP_SECRET_ACCESS_KEY",
    ),
}

DESTINATION_LABELS = {
    "r2": "Cloudflare R2（主）",
    "backup": "別系統の S3 互換保管先（副）",
}

#: 持続性の基準を満たす宛先数（DESIGN.md 第9章）
REQUIRED_DESTINATIONS = 2


class OffloadError(RuntimeError):
    pass


@dataclass
class Destination:
    name: str
    endpoint: str | None = None
    bucket: str | None = None
    access_key: str | None = None
    secret_key: str | None = None

    @property
    def label(self) -> str:
        return DESTINATION_LABELS.get(self.name, self.name)

    @property
    def missing(self) -> list[str]:
        env = DESTINATION_ENV.get(self.name, ())
        values = (self.endpoint, self.bucket, self.access_key, self.secret_key)
        return [name for name, value in zip(env, values, strict=False) if not value]

    @property
    def configured(self) -> bool:
        return not self.missing

    def as_client_kwargs(self) -> dict[str, str | None]:
        return {
            "endpoint_url": self.endpoint,
            "aws_access_key_id": self.access_key,
            "aws_secret_access_key": self.secret_key,
        }


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
class DestinationResult:
    """1宛先ぶんの結果。**宛先ごとに分けて数える。**

    片方だけ失敗したときに「何件送れたか」を宛先ごとに言えないと、
    どちらを再実行すればよいか分からない。
    """

    name: str
    bucket: str | None = None
    prefix: str | None = None
    uploaded: int = 0
    uploaded_bytes: int = 0
    failed: int = 0
    skipped: bool = False
    reason: str | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "bucket": self.bucket,
            "prefix": self.prefix,
            "uploaded": self.uploaded,
            "uploaded_bytes": self.uploaded_bytes,
            "failed": self.failed,
            "skipped": self.skipped,
            "reason": self.reason,
            "errors": self.errors[:5],
        }


@dataclass
class OffloadResult:
    """全宛先の合計。個別の内訳は `by_destination` にある。"""

    by_destination: dict[str, DestinationResult] = field(default_factory=dict)
    skipped: bool = False
    reason: str | None = None
    #: 宛先が1つしかない。**送れていても持続性の基準は満たしていない**
    single_destination: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def uploaded(self) -> int:
        return sum(d.uploaded for d in self.by_destination.values())

    @property
    def uploaded_bytes(self) -> int:
        return sum(d.uploaded_bytes for d in self.by_destination.values())

    @property
    def failed(self) -> int:
        return sum(d.failed for d in self.by_destination.values())

    @property
    def errors(self) -> list[str]:
        out: list[str] = []
        for d in self.by_destination.values():
            out.extend(f"[{d.name}] {e}" for e in d.errors)
        return out

    @property
    def destinations_used(self) -> list[str]:
        return sorted(d.name for d in self.by_destination.values() if not d.skipped)

    @property
    def bucket(self) -> str | None:
        """後方互換。主宛先のバケット名。"""
        primary = self.by_destination.get("r2")
        return primary.bucket if primary else None

    @property
    def prefix(self) -> str | None:
        primary = self.by_destination.get("r2")
        if primary:
            return primary.prefix
        for d in self.by_destination.values():
            return d.prefix
        return None

    def to_dict(self) -> dict:
        return {
            "uploaded": self.uploaded,
            "uploaded_bytes": self.uploaded_bytes,
            "failed": self.failed,
            "skipped": self.skipped,
            "reason": self.reason,
            "bucket": self.bucket,
            "prefix": self.prefix,
            "single_destination": self.single_destination,
            "destinations_used": self.destinations_used,
            "required_destinations": REQUIRED_DESTINATIONS,
            "by_destination": {
                name: d.to_dict() for name, d in sorted(self.by_destination.items())
            },
            "errors": self.errors[:5],
            "notes": self.notes,
        }


def destinations() -> list[Destination]:
    """設定されている宛先を返す。`.env` から読む。コードにも設定にも書かない。"""
    out: list[Destination] = []
    for name, env in DESTINATION_ENV.items():
        endpoint, bucket, access, secret = (credential(k) for k in env)
        out.append(
            Destination(
                name=name,
                endpoint=endpoint,
                bucket=bucket,
                access_key=access,
                secret_key=secret,
            )
        )
    return out


def configured_destinations() -> list[Destination]:
    return [d for d in destinations() if d.configured]


def settings() -> dict[str, str | None]:
    """主宛先（R2）の接続情報。後方互換のために残している。"""
    primary = next((d for d in destinations() if d.name == "r2"), Destination("r2"))
    return {
        "endpoint": primary.endpoint,
        "bucket": primary.bucket,
        "access_key": primary.access_key,
        "secret_key": primary.secret_key,
    }


def missing_settings() -> list[str]:
    """主宛先で足りていないキー。後方互換のために残している。"""
    primary = next((d for d in destinations() if d.name == "r2"), Destination("r2"))
    return primary.missing


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


def _client(destination: Destination):
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - 依存が無い環境向け
        raise OffloadError(
            "boto3 がありません。pip install 'mailauth-observatory[offload]' を実行してください"
        ) from exc

    return boto3.client("s3", region_name="auto", **destination.as_client_kwargs())


def _send(
    the_plan: OffloadPlan,
    destination: Destination,
    *,
    run_id: str,
    client,
) -> DestinationResult:
    """1宛先に送る。**1件の失敗で全体を止めない。**"""
    result = DestinationResult(
        name=destination.name,
        bucket=destination.bucket,
        prefix=f"runs/{run_id}",
    )
    root = run_dir(run_id)
    for path in the_plan.files:
        key = f"{result.prefix}/{path.relative_to(root).as_posix()}"
        try:
            client.upload_file(str(path), destination.bucket, key)
        except Exception as exc:  # noqa: BLE001 - 1件の失敗で全体を止めない
            result.failed += 1
            if len(result.errors) < 10:
                result.errors.append(f"{key}: {exc}")
            continue
        result.uploaded += 1
        result.uploaded_bytes += path.stat().st_size
    return result


def offload(
    run_id: str,
    *,
    dry_run: bool = False,
    dirs: tuple[str, ...] = OFFLOAD_DIRS,
    client=None,
    clients: dict[str, object] | None = None,
) -> tuple[OffloadPlan, OffloadResult]:
    """bronze / silver を設定されたすべての宛先に送る。

    認証情報が無ければ**何もせずに理由を返す。** 黙って成功したふりを
    すると、退避されていないことに気付くのはリポジトリを失ったあとになる。

    `client` は主宛先だけに使う後方互換の口。複数宛先を注入するときは
    `clients={"r2": ..., "backup": ...}` を渡す。
    """
    the_plan = plan(run_id, dirs=dirs)
    result = OffloadResult()

    injected = dict(clients or {})
    if client is not None and "r2" not in injected:
        injected["r2"] = client

    targets = [
        d for d in destinations() if d.configured or d.name in injected
    ]

    if not targets:
        missing = {d.name: d.missing for d in destinations()}
        result.skipped = True
        result.reason = (
            "退避先の設定が足りないため退避していない: "
            + "; ".join(f"{name}={', '.join(keys)}" for name, keys in missing.items())
            + "。bronze を失うと過去の再解釈ができなくなる"
        )
        return the_plan, result

    result.single_destination = len(targets) < REQUIRED_DESTINATIONS
    if result.single_destination:
        # **送れたことと持続性の基準を満たしたことは別である。**
        # DESIGN.md 第9章は R2 以外の保管先を要求している
        unset = [d.name for d in destinations() if not d.configured]
        result.notes.append(
            f"退避先が {len(targets)} か所しかない。DESIGN.md 第9章は"
            f"{REQUIRED_DESTINATIONS} か所（R2 以外の保管先）を求めている。"
            f"未設定の宛先: {', '.join(unset) or '（なし）'}。"
            "**送れていても持続性の基準は満たしていない**"
        )

    if dry_run:
        result.skipped = True
        result.reason = (
            f"dry_run のため送っていない（対象 {the_plan.count} 件 × "
            f"{len(targets)} 宛先）"
        )
        for d in targets:
            result.by_destination[d.name] = DestinationResult(
                name=d.name,
                bucket=d.bucket,
                prefix=f"runs/{run_id}",
                skipped=True,
                reason="dry_run",
            )
        return the_plan, result

    for destination in targets:
        s3 = injected.get(destination.name) or _client(destination)
        sent = _send(the_plan, destination, run_id=run_id, client=s3)
        if sent.failed:
            sent.reason = (
                f"{sent.failed} 件を送れなかった。bronze が欠けると"
                "その月の再解釈ができなくなるので、再実行すること"
            )
        result.by_destination[destination.name] = sent

    failed_names = [d.name for d in result.by_destination.values() if d.failed]
    if failed_names:
        result.reason = (
            f"{result.failed} 件を送れなかった（{', '.join(failed_names)}）。"
            "bronze が欠けるとその月の再解釈ができなくなるので、再実行すること"
        )
    return the_plan, result
