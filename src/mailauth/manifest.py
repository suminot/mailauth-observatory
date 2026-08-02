"""工程メトリクスの記録（DESIGN.md 5.2、原則4）。

すべてのフェーズは実行の最後に `_manifest.json` を書く。運用コンソールは
これを読んで工程を可視化するので、フェーズが例外で落ちた場合でも
manifest は必ず残さなければならない。だからコンテキストマネージャにしてある。

    with RunManifest(run_id="2026-08", phase="p1_population", out_dir=d) as m:
        m.counts.input = 3000
        ...
        m.add_output("entities.parquet", records=1552, bytes=123456)

例外が出た場合は status="failed" と error を書いてから再送出する。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
import traceback
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import __version__

MANIFEST_FILENAME = "_manifest.json"

STATUS_SUCCESS = "success"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _iso(ts: dt.datetime) -> str:
    return ts.astimezone(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class Counts:
    input: int = 0
    success: int = 0
    failed: int = 0
    skipped: int = 0


@dataclass
class OutputRef:
    path: str
    records: int | None = None
    bytes: int | None = None


@dataclass
class Warning_:
    code: str
    count: int
    sample: list[str] = field(default_factory=list)
    message: str | None = None


def config_hash(*paths: Path | str) -> str:
    """設定ファイル群の内容ハッシュ。どの設定で回した結果かを後から特定できる。"""
    h = hashlib.sha256()
    for p in sorted(str(x) for x in paths):
        path = Path(p)
        if path.is_file():
            h.update(path.name.encode())
            h.update(path.read_bytes())
    return f"sha256:{h.hexdigest()}"


def _git_revision() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


class RunManifest:
    """1フェーズ1実行の記録。

    `counts` の値は各フェーズが自分で設定する。status はそこから導出する。
      - 例外で落ちた            -> failed
      - counts.failed > 0       -> partial（出力は生成されている）
      - それ以外                -> success
    """

    def __init__(
        self,
        run_id: str,
        phase: str,
        out_dir: Path | str,
        *,
        tool_versions: dict[str, str] | None = None,
        config_hash: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> None:
        self.run_id = run_id
        self.phase = phase
        self.out_dir = Path(out_dir)
        self.tool_versions = {"mailauth": __version__, **(tool_versions or {})}
        rev = _git_revision()
        if rev:
            self.tool_versions.setdefault("git", rev)
        self.config_hash = config_hash
        self.params = params or {}

        self.started_at = _utcnow()
        self.finished_at: dt.datetime | None = None
        self.counts = Counts()
        self.failure_breakdown: Counter[str] = Counter()
        self.breakdown: dict[str, Any] = {}
        self.warnings: list[Warning_] = []
        self.outputs: list[OutputRef] = []
        self.attribution: list[str] = []
        self.error: str | None = None
        self._forced_status: str | None = None

    # -- 記録 --------------------------------------------------------------

    def add_failure(self, reason: str, n: int = 1) -> None:
        """失敗を理由別に数える。counts.failed も同時に進む。"""
        self.failure_breakdown[reason] += n
        self.counts.failed += n

    def add_warning(
        self, code: str, count: int = 1, sample: list[str] | None = None, message: str | None = None
    ) -> None:
        """同じ code の警告は1件にまとめ、サンプルは先頭5件だけ残す。"""
        for w in self.warnings:
            if w.code == code:
                w.count += count
                for s in sample or []:
                    if len(w.sample) < 5 and s not in w.sample:
                        w.sample.append(s)
                if message and not w.message:
                    w.message = message
                return
        self.warnings.append(
            Warning_(code=code, count=count, sample=list((sample or [])[:5]), message=message)
        )

    def add_output(self, path: str, records: int | None = None, bytes_: int | None = None) -> None:
        resolved = self.out_dir / path
        if bytes_ is None and resolved.is_file():
            bytes_ = resolved.stat().st_size
        self.outputs.append(OutputRef(path=path, records=records, bytes=bytes_))

    def set_breakdown(self, **kwargs: Any) -> None:
        """フェーズ固有のメトリクス（DESIGN.md 第6章の breakdown）。"""
        self.breakdown.update(kwargs)

    def force_status(self, status: str) -> None:
        self._forced_status = status

    # -- 導出 --------------------------------------------------------------

    @property
    def status(self) -> str:
        if self._forced_status:
            return self._forced_status
        if self.error:
            return STATUS_FAILED
        if self.counts.failed > 0:
            return STATUS_PARTIAL
        return STATUS_SUCCESS

    def to_dict(self) -> dict[str, Any]:
        finished = self.finished_at or _utcnow()
        d: dict[str, Any] = {
            "run_id": self.run_id,
            "phase": self.phase,
            "started_at": _iso(self.started_at),
            "finished_at": _iso(finished),
            "duration_sec": round((finished - self.started_at).total_seconds(), 3),
            "status": self.status,
            "tool_versions": self.tool_versions,
            "config_hash": self.config_hash,
            "params": self.params,
            "counts": asdict(self.counts),
            "failure_breakdown": dict(sorted(self.failure_breakdown.items())),
            "breakdown": self.breakdown,
            "warnings": [asdict(w) for w in self.warnings],
            "outputs": [asdict(o) for o in self.outputs],
        }
        if self.attribution:
            d["attribution"] = self.attribution
        if self.error:
            d["error"] = self.error
        return d

    def write(self) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / MANIFEST_FILENAME
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return path

    # -- コンテキストマネージャ ----------------------------------------------

    def __enter__(self) -> RunManifest:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.finished_at = _utcnow()
        if exc is not None:
            self.error = "".join(traceback.format_exception_only(exc_type, exc)).strip()
            self.breakdown.setdefault("traceback", traceback.format_exc()[-4000:])
        self.write()
        return False  # 例外は握りつぶさない


def read_manifest(path: Path | str) -> dict[str, Any] | None:
    """manifest を読む。存在しない・壊れている場合は None。

    コンソールが未実行のフェーズを表示するために使うので、
    「無い」ことは異常ではなく正常な状態として扱う。
    """
    p = Path(path)
    if p.is_dir():
        p = p / MANIFEST_FILENAME
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
