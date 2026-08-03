"""未実装フェーズの共通スタブ。

**現在は全フェーズが実装済みなので `PLANNED_SPRINT` は空**である。
仕組みは残す。フェーズを追加したときに「何もせず成功したふりをする」
実装が入り込むのを防ぐため。空の出力を作ると、下流が「0件だった」と
解釈してしまう。

新しいフェーズを足すときは、実装前に PLANNED_SPRINT に登録して
CLI から理由付きで止まるようにする。
"""

from __future__ import annotations

from .manifest import STATUS_FAILED, RunManifest
from .paths import phase_dir

#: フェーズ -> 実装予定スプリント（DESIGN.md 第8章）。
#: 実装済みのフェーズはここから外す。cli.IMPLEMENTED と排他であることを
#: tests/test_cli_and_console.py が確認している
PLANNED_SPRINT: dict[str, str] = {}


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
