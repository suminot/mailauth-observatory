"""画面4 手法比較 ── 複数バックエンド・複数リゾルバの観測差分。

P4 が書いた `compare.json` を読む。**コンソールで再計算しない。**
CLI で見た数字と画面の数字が違うと、比較そのものが信用できなくなる。

差分の分類をそのまま画面に出す。「両方が観測できたのに食い違う」ことと
「片方が引けなかった」ことは、運用上まったく意味が違う（原則5）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from mailauth.p4_measure.compare import available_methods
from mailauth.p4_measure.compare_runner import read_compare
from mailauth.paths import run_dir

router = APIRouter(prefix="/api/compare", tags=["compare"])

#: 分類の説明。画面で意味を取り違えないようにする
KIND_LABELS = {
    "agree": "一致",
    "presence_differs": "レコードの有無が食い違う（両方観測できている）",
    "values_differ": "値が違う（両方レコードあり）",
    "observation_differs": "片方が観測できていない ── 食い違いではない",
    "only_in": "同じ purpose 内で片方にしか無いクエリ",
}


@router.get("/{run_id}")
def compare(run_id: str) -> dict[str, Any]:
    if not run_dir(run_id).is_dir():
        raise HTTPException(status_code=404, detail=f"run {run_id} がありません")

    payload = read_compare(run_id)
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"{run_id} の比較結果がありません。"
                "mailauth p4-compare を実行してください"
            ),
        )
    return {
        **payload,
        "kind_labels": KIND_LABELS,
        # bronze に今あるパーティション。比較済みのものと差があれば画面で分かる
        "bronze_methods": available_methods(run_id),
    }
