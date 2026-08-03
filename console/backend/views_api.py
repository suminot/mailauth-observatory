"""ビュー切り替え（画面7）。

同じ計測結果を、全上場×業種軸 / プライムのみ、と切り替えて見るための API。
絞り込みのロジックは CLI と同じ mailauth.views を呼ぶ。
コンソールと CLI で違う数字が出ると信用できなくなるため。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from mailauth.io import read_parquet
from mailauth.paths import phase_output
from mailauth.views import GROUP_BY_COLUMNS, get_view, list_views, summarize

router = APIRouter(prefix="/api", tags=["views"])


@router.get("/views")
def get_views() -> dict[str, Any]:
    return {
        "views": [
            {
                "id": v.id,
                "label": v.label,
                "description": v.description,
                "default_group_by": v.default_group_by,
                "requires_segment": v.requires_segment,
            }
            for v in list_views()
        ],
        "group_by_options": sorted(GROUP_BY_COLUMNS),
    }


@router.get("/runs/{run_id}/view")
def run_view(
    run_id: str,
    view: str = Query("jp-all"),
    by: str | None = Query(None),
) -> dict[str, Any]:
    df = read_parquet(phase_output(run_id, "p1_population", "entities.parquet"))
    if df is None:
        raise HTTPException(
            status_code=404,
            detail=f"run {run_id} の entities.parquet がありません。先に P1 を実行してください",
        )
    try:
        cfg = get_view(view)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        return summarize(df, cfg, group_by=by).to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
