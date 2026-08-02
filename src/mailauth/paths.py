"""実行ディレクトリの規約（DESIGN.md 5.1）。

フェーズ間の結合はファイルパスの規約のみ（原則3）。その規約をここ1か所に置く。
"""

from __future__ import annotations

import datetime as dt
import os
import re
from pathlib import Path

RUN_ID_RE = re.compile(r"^\d{4}-\d{2}(-[A-Za-z0-9._-]+)?$")


def repo_root() -> Path:
    """リポジトリのルート。環境変数で上書きできる（テスト・コンソール用）。"""
    env = os.environ.get("MAILAUTH_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[2]


def data_root() -> Path:
    env = os.environ.get("MAILAUTH_DATA_ROOT")
    return Path(env).resolve() if env else repo_root() / "data"


def runs_root() -> Path:
    return data_root() / "runs"


def cache_root() -> Path:
    return data_root() / "cache"


def gold_root() -> Path:
    env = os.environ.get("MAILAUTH_GOLD_ROOT")
    return Path(env).resolve() if env else repo_root() / "gold"


def default_run_id(today: dt.date | None = None) -> str:
    d = today or dt.date.today()
    return f"{d.year:04d}-{d.month:02d}"


def validate_run_id(run_id: str) -> str:
    if not RUN_ID_RE.match(run_id):
        raise ValueError(
            f"run_id の形式が不正です: {run_id!r}。YYYY-MM または YYYY-MM-<suffix> を使ってください"
        )
    return run_id


def run_dir(run_id: str) -> Path:
    return runs_root() / validate_run_id(run_id)


def phase_dir(run_id: str, phase: str) -> Path:
    return run_dir(run_id) / phase


def phase_output(run_id: str, phase: str, filename: str) -> Path:
    return phase_dir(run_id, phase) / filename


def bronze_dir(run_id: str, method: str | None = None) -> Path:
    d = phase_dir(run_id, "p4_measure") / "bronze"
    return d / f"method={method}" if method else d


def gold_dir(month: str) -> Path:
    return gold_root() / f"month={month}"


def previous_run_id(run_id: str) -> str:
    """前月の run_id。差分検出に使う。サフィックス付きは基本形に戻す。"""
    base = run_id.split("-")[0] + "-" + run_id.split("-")[1]
    year, month = (int(x) for x in base.split("-"))
    if month == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{month - 1:02d}"


def month_date(run_id: str) -> dt.date:
    year, month = (int(x) for x in run_id.split("-")[:2])
    return dt.date(year, month, 1)


def config_path(relative: str) -> Path:
    """configs/ 以下の相対パスを絶対パスに解決する。絶対パスはそのまま通す。"""
    p = Path(relative)
    return p if p.is_absolute() else repo_root() / p


def list_run_ids() -> list[str]:
    root = runs_root()
    if not root.is_dir():
        return []
    return sorted((d.name for d in root.iterdir() if d.is_dir() and RUN_ID_RE.match(d.name)),
                  reverse=True)
