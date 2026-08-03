"""画面6 月次差分 ── gold を読んで前月比を出す。

P7 が書いた `gold/month=YYYY-MM/` を読む。**gold を再計算しない。**
コンソールで見える数字と公開サイトに出る数字が違うと信用できなくなるため、
表示は P7 の出力そのままにする。

「消えた」と「取れなかった」を分けて出すのはここでも同じ。画面上で
`domains_unobserved_this_month` を `domains_disappeared` の隣に並べておかないと、
運用者が「先月あったのに消えた」と読んでしまう。
"""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from mailauth.io import read_parquet
from mailauth.p7_aggregate import BY_SECTOR_FILENAME, OVERALL_FILENAME
from mailauth.p7_aggregate.delta import diff_stats
from mailauth.paths import gold_dir, gold_root, previous_run_id

router = APIRouter(prefix="/api/gold", tags=["gold"])

MONTH_DIR_RE = re.compile(r"^month=(\d{4}-\d{2})$")


def available_months() -> list[str]:
    root = gold_root()
    if not root.is_dir():
        return []
    months = []
    for d in root.iterdir():
        m = MONTH_DIR_RE.match(d.name)
        if d.is_dir() and m:
            months.append(m.group(1))
    return sorted(months, reverse=True)


def _jsonable(value: Any) -> Any:
    import datetime as _dt

    import pandas as pd

    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if hasattr(value, "tolist") and not isinstance(value, str):
        return value.tolist()
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.isoformat()
    return value


def _rows(month: str, filename: str) -> list[dict] | None:
    frame = read_parquet(gold_dir(month) / filename)
    if frame is None:
        return None
    return [
        {k: _jsonable(v) for k, v in record.items()}
        for record in frame.to_dict(orient="records")
    ]


@router.get("/months")
def months() -> dict[str, Any]:
    return {"months": available_months()}


@router.get("/{month}")
def month_detail(
    month: str, population: str | None = Query(None)
) -> dict[str, Any]:
    """指定月の全社統計・業種別集計と、前月比。"""
    overall = _rows(month, OVERALL_FILENAME)
    if overall is None:
        raise HTTPException(
            status_code=404,
            detail=f"{month} の gold がありません。先に p7-aggregate を実行してください",
        )
    sectors = _rows(month, BY_SECTOR_FILENAME) or []

    if population:
        overall = [r for r in overall if r.get("population_id") == population]
        sectors = [r for r in sectors if r.get("population_id") == population]
        if not overall:
            raise HTTPException(
                status_code=404, detail=f"母集団 {population} の集計がありません"
            )

    prev_month = previous_run_id(month)
    prev_overall = {
        r["population_id"]: r for r in (_rows(prev_month, OVERALL_FILENAME) or [])
    }

    out = []
    for row in overall:
        previous = prev_overall.get(row["population_id"])
        out.append(
            {
                **row,
                "maturity_stage_dist": _parse_json(row.get("maturity_stage_dist")),
                # P7 が計算した差分。ここで作り直さない
                "delta_prev_month": _parse_json(row.get("delta_prev_month")),
                # 主要指標の増減。前月の gold が無ければ null
                "diff_prev_month": (
                    diff_stats(_Obj(row), _Obj(previous)) if previous else None
                ),
                "previous_month": prev_month if previous else None,
            }
        )

    return {
        "month": month,
        "previous_month": prev_month,
        "overall": out,
        "by_sector": [
            {**r, "maturity_stage_dist": _parse_json(r.get("maturity_stage_dist"))}
            for r in sectors
        ],
        # 秘匿したセルがあることを画面に出す。黙って束ねると分母を誤解する
        "suppressed_sectors": [
            r["common12_code"] for r in sectors if r.get("suppressed")
        ],
    }


class _Obj:
    """dict を属性アクセスにする薄いラッパ。diff_stats を使い回すため。"""

    def __init__(self, data: dict) -> None:
        self._data = data

    def __getattr__(self, name: str) -> Any:
        return self._data.get(name)


def _parse_json(value: Any) -> Any:
    if not value or not isinstance(value, str):
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None
