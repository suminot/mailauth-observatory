"""実行記録の集約（DESIGN.md Sprint 8「自動化」）。

月次実行を人手を介さず回すには、**終わったあとに何が起きたかを1つの
ファイルから読めること**が要る。各フェーズの `_manifest.json` は
それぞれ完結しているが、8つに散っていると「今月は成功したのか」が
一目で分からない。

ここが返すもの
  - 工程ごとの status / 件数 / 警告
  - 工程間で件数がどう変わったか（**どこで何件落ちたか**が最重要）
  - 全体の成否。CI がこれで終了コードを決める

**「失敗」と「未実行」を分ける。** 実行していない工程を失敗として扱うと、
部分実行を意図的にやったときに毎回赤くなる（原則5 の延長）。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from . import PHASE_LABELS, PHASES
from .manifest import (
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_SUCCESS,
    read_manifest,
)
from .paths import phase_dir, run_dir

NOT_RUN = "not_run"

#: 全体を失敗と見なす status
FAILING = (STATUS_FAILED,)


@dataclass
class PhaseReport:
    phase: str
    label: str
    status: str = NOT_RUN
    input: int | None = None
    success: int | None = None
    failed: int | None = None
    skipped: int | None = None
    duration_sec: float | None = None
    warnings: list[dict] = field(default_factory=list)
    failure_breakdown: dict[str, int] = field(default_factory=dict)
    error: str | None = None
    outputs: list[dict] = field(default_factory=list)

    @property
    def ran(self) -> bool:
        return self.status != NOT_RUN


@dataclass
class RunReport:
    run_id: str
    phases: list[PhaseReport] = field(default_factory=list)
    #: 工程間の件数変化。どこで落ちたかを追うための主要な材料
    flow: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ran_phases(self) -> list[PhaseReport]:
        return [p for p in self.phases if p.ran]

    @property
    def failed_phases(self) -> list[PhaseReport]:
        return [p for p in self.phases if p.status in FAILING]

    @property
    def partial_phases(self) -> list[PhaseReport]:
        return [p for p in self.phases if p.status == STATUS_PARTIAL]

    @property
    def status(self) -> str:
        """全体の status。

        **未実行は失敗にしない。** 部分実行を意図的にやったときに
        毎回赤くなると、赤が意味を持たなくなる。
        """
        if not self.ran_phases:
            return NOT_RUN
        if self.failed_phases:
            return STATUS_FAILED
        if self.partial_phases:
            return STATUS_PARTIAL
        return STATUS_SUCCESS

    @property
    def total_warnings(self) -> int:
        return sum(len(p.warnings) for p in self.phases)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "phases": [
                {
                    "phase": p.phase,
                    "label": p.label,
                    "status": p.status,
                    "input": p.input,
                    "success": p.success,
                    "failed": p.failed,
                    "skipped": p.skipped,
                    "duration_sec": p.duration_sec,
                    "warnings": [w.get("code") for w in p.warnings],
                    "error": p.error,
                }
                for p in self.phases
            ],
            "flow": self.flow,
            "total_warnings": self.total_warnings,
            "failed_phases": [p.phase for p in self.failed_phases],
            "not_run": [p.phase for p in self.phases if not p.ran],
        }


def collect(run_id: str) -> RunReport:
    """八工程の manifest を読んで1つにまとめる。"""
    report = RunReport(run_id=run_id)

    for phase in PHASES:
        manifest = read_manifest(phase_dir(run_id, phase))
        item = PhaseReport(phase=phase, label=PHASE_LABELS[phase])
        if manifest is not None:
            counts = manifest.get("counts") or {}
            item.status = str(manifest.get("status") or NOT_RUN)
            item.input = counts.get("input")
            item.success = counts.get("success")
            item.failed = counts.get("failed")
            item.skipped = counts.get("skipped")
            item.duration_sec = manifest.get("duration_sec")
            item.warnings = list(manifest.get("warnings") or [])
            item.failure_breakdown = dict(manifest.get("failure_breakdown") or {})
            item.error = manifest.get("error")
            item.outputs = list(manifest.get("outputs") or [])
        report.phases.append(item)

    # 工程間の件数変化。実行した工程だけを順に並べる
    ran = report.ran_phases
    for before, after in zip(ran, ran[1:], strict=False):
        report.flow.append(
            {
                "from": before.phase,
                "to": after.phase,
                "out": before.success,
                "in": after.input,
                # 出力と入力がずれるのは正常（P3 の絞り込み、P4 の1ドメイン
                # 複数クエリなど）。ずれ自体ではなく、その大きさを見る
                "delta": (
                    (after.input - before.success)
                    if after.input is not None and before.success is not None
                    else None
                ),
            }
        )

    return report


def to_markdown(report: RunReport, *, now: dt.datetime | None = None) -> str:
    """人が読む形にする。GitHub Actions の成果物として commit する。"""
    stamp = (now or dt.datetime.now(dt.UTC)).replace(microsecond=0).isoformat()
    lines = [
        f"# 月次計測 {report.run_id}",
        "",
        f"- 状態: **{report.status}**",
        f"- 集計時刻: {stamp}",
        f"- 警告: {report.total_warnings} 件",
        "",
        "## 工程",
        "",
        "| 工程 | 状態 | 入力 | 成功 | 失敗 | スキップ | 秒 |",
        "|---|---|---|---|---|---|---|",
    ]
    for p in report.phases:
        lines.append(
            f"| {p.phase} {p.label} | {p.status} | {_n(p.input)} | {_n(p.success)} "
            f"| {_n(p.failed)} | {_n(p.skipped)} | {_n(p.duration_sec)} |"
        )

    if report.flow:
        lines += [
            "",
            "## 工程間の件数",
            "",
            "どこで何件落ちたかを見るための表である。出力と入力がずれること自体は",
            "正常で（P3 の絞り込み、P4 の1ドメイン複数クエリなど）、見るのはその大きさ。",
            "",
            "| 前 | 後 | 前の成功 | 後の入力 | 差 |",
            "|---|---|---|---|---|",
        ]
        for f in report.flow:
            lines.append(
                f"| {f['from']} | {f['to']} | {_n(f['out'])} | {_n(f['in'])} "
                f"| {_n(f['delta'])} |"
            )

    warned = [p for p in report.phases if p.warnings]
    if warned:
        lines += ["", "## 警告", ""]
        for p in warned:
            lines.append(f"### {p.phase}")
            lines.append("")
            for w in p.warnings:
                count = w.get("count")
                suffix = f" ×{count}" if count and count > 1 else ""
                lines.append(f"- `{w.get('code')}`{suffix}: {w.get('message') or ''}")
            lines.append("")

    failed = report.failed_phases
    if failed:
        lines += ["", "## 失敗した工程", ""]
        for p in failed:
            lines.append(f"- **{p.phase}**: {p.error or '(理由の記録なし)'}")
            for code, n in sorted(p.failure_breakdown.items()):
                lines.append(f"  - {code}: {n}")

    not_run = [p for p in report.phases if not p.ran]
    if not_run:
        lines += [
            "",
            "## 未実行の工程",
            "",
            "**失敗ではない。** 実行していない工程を失敗として扱うと、",
            "部分実行を意図的にやったときに毎回赤くなる。",
            "",
        ]
        lines += [f"- {p.phase} {p.label}" for p in not_run]

    return "\n".join(lines) + "\n"


def _n(value: Any) -> str:
    return "—" if value is None else str(value)


def exists(run_id: str) -> bool:
    return run_dir(run_id).is_dir()
