"""run の一覧・詳細・manifest 読み込み（画面1 パイプライン全景）。

工程間の矢印に件数の変化を出すのが最重要要件なので、
各フェーズの counts をそのまま返し、差分の解釈はフロントに任せる。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from mailauth import PHASE_LABELS, PHASE_OUTPUTS, PHASES
from mailauth.config import list_populations
from mailauth.manifest import read_manifest
from mailauth.paths import list_run_ids, phase_dir, run_dir

router = APIRouter(prefix="/api", tags=["runs"])


def _phase_view(run_id: str, phase: str) -> dict[str, Any]:
    manifest = read_manifest(phase_dir(run_id, phase))
    return {
        "phase": phase,
        "label": PHASE_LABELS[phase],
        "output": PHASE_OUTPUTS[phase],
        # manifest が無い = そのフェーズをまだ実行していない。異常ではない
        "status": (manifest or {}).get("status", "not_run"),
        "started_at": (manifest or {}).get("started_at"),
        "finished_at": (manifest or {}).get("finished_at"),
        "duration_sec": (manifest or {}).get("duration_sec"),
        "counts": (manifest or {}).get("counts"),
        "failure_breakdown": (manifest or {}).get("failure_breakdown", {}),
        "warnings": (manifest or {}).get("warnings", []),
        "outputs": (manifest or {}).get("outputs", []),
        "breakdown": (manifest or {}).get("breakdown", {}),
        "error": (manifest or {}).get("error"),
        "attribution": (manifest or {}).get("attribution", []),
    }


@router.get("/runs")
def get_runs() -> dict[str, Any]:
    return {"runs": list_run_ids()}


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    if not run_dir(run_id).is_dir():
        raise HTTPException(status_code=404, detail=f"run {run_id} がありません")
    return {"run_id": run_id, "phases": [_phase_view(run_id, p) for p in PHASES]}


@router.get("/runs/{run_id}/phases/{phase}")
def get_phase(run_id: str, phase: str) -> dict[str, Any]:
    if phase not in PHASES:
        raise HTTPException(status_code=404, detail=f"不明なフェーズ: {phase}")
    return _phase_view(run_id, phase)


@router.get("/populations")
def get_populations() -> dict[str, Any]:
    """画面2 の母集団プリセット。未実装のものも理由付きで返す。"""
    out = []
    for cfg in list_populations():
        out.append(
            {
                "id": cfg.id,
                "label": cfg.label,
                "country": cfg.country,
                "enabled": cfg.enabled,
                "implemented": cfg.implemented,
                "blocked_by": cfg.blocked_by,
                "path": str(cfg.source_path),
                "attribution": cfg.attribution,
            }
        )
    return {"populations": out}
