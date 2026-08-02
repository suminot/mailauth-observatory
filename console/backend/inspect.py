"""レコード検査（画面3）── Sprint 2 で実装。

任意のフェーズの出力を DuckDB でクエリし、ドメイン名で串刺しに検索して
bronze の生 JSON から gold への寄与までを縦に並べる。
「なぜこの判定になったか」を追跡する主要な手段になる画面なので、
実装するときは P2/P3 の出力が揃ってからにする。

Sprint 1 の時点では、P1 の出力だけ覗ける最小の入口を提供している。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from mailauth import PHASES
from mailauth.io import read_parquet
from mailauth.paths import phase_dir

router = APIRouter(prefix="/api/inspect", tags=["inspect"])

MAX_ROWS = 500


@router.get("/{run_id}/{phase}")
def preview(
    run_id: str,
    phase: str,
    filename: str = Query("entities.parquet"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=MAX_ROWS),
    q: str | None = Query(None, description="全列に対する部分一致フィルタ"),
) -> dict[str, Any]:
    if phase not in PHASES:
        raise HTTPException(status_code=404, detail=f"不明なフェーズ: {phase}")
    path = phase_dir(run_id, phase) / filename
    df = read_parquet(path)
    if df is None:
        raise HTTPException(status_code=404, detail=f"出力がありません: {path}")

    if q:
        mask = df.astype(str).apply(lambda col: col.str.contains(q, case=False, na=False))
        df = df[mask.any(axis=1)]

    total = len(df)
    page = df.iloc[offset : offset + limit]
    rows = [
        {k: _jsonable(v) for k, v in record.items()}
        for record in page.to_dict(orient="records")
    ]
    return {
        "path": str(path),
        "total": total,
        "offset": offset,
        "limit": limit,
        "columns": list(df.columns),
        "rows": rows,
    }


def _jsonable(value: Any) -> Any:
    """Parquet 由来の値を JSON にできる形に落とす。

    Parquet の list 列は numpy 配列で、日付は date オブジェクトで返る。
    どちらもそのままでは JSON 化できない。
    """
    import datetime as _dt

    import pandas as pd

    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if value is pd.NaT:
        return None
    if hasattr(value, "tolist") and not isinstance(value, str):
        return _jsonable(value.tolist()) if hasattr(value, "dtype") else value.tolist()
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.isoformat()
    return value
