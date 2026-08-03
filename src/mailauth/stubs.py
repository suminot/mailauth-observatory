"""未実装フェーズの共通スタブ。

Sprint 1 の時点では P2〜P8 は未実装である。呼ばれたら、何もせず
成功したふりをするのではなく、どのスプリントで実装予定かを添えて止める。
空の出力を作ってしまうと、下流が「0件だった」と解釈しうるため。
"""

from __future__ import annotations

from .manifest import STATUS_FAILED, RunManifest
from .paths import phase_dir

#: フェーズ -> 実装予定スプリント（DESIGN.md 第8章）
PLANNED_SPRINT = {
    "p2_candidates": "Sprint 2",
    "p3_domains": "Sprint 2",
    "p4_measure": "Sprint 3",
    "p5_parse": "Sprint 4",
    "p6_infer": "Sprint 5",
    "p7_aggregate": "Sprint 6",
    "p8_publish": "Sprint 7",
}


class PhaseNotImplementedError(NotImplementedError):
    pass


def not_implemented(phase: str, run_id: str) -> None:
    """未実装フェーズ。manifest だけは残してから止める（原則4）。"""
    sprint = PLANNED_SPRINT.get(phase, "未定")
    message = f"{phase} は未実装です（{sprint} で実装予定。DESIGN.md 第8章）"
    manifest = RunManifest(run_id=run_id, phase=phase, out_dir=phase_dir(run_id, phase))
    manifest.error = message
    manifest.force_status(STATUS_FAILED)
    manifest.set_breakdown(planned_sprint=sprint, implemented=False)
    manifest.write()
    raise PhaseNotImplementedError(message)
