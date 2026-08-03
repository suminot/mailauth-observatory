"""八工程の CLI（DESIGN.md 3.2）。

各フェーズは独立したサブコマンドとして実装する。前後のフェーズを知らず、
ファイルを入力にファイルを出力する（原則3）。

    mailauth p1-population --config configs/populations/jp-prime.yaml --run 2026-08
    mailauth p2-candidates --run 2026-08
    ...
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from . import PHASE_LABELS, PHASES, __version__
from .config import list_populations
from .paths import default_run_id, list_run_ids, phase_dir, validate_run_id
from .stubs import PhaseNotImplementedError, not_implemented

app = typer.Typer(
    name="mailauth",
    help="メール認証月次計測システム。設計仕様は DESIGN.md を参照。",
    no_args_is_help=True,
    add_completion=False,
)

RunOption = Annotated[
    str, typer.Option("--run", help="実行ID。既定は当月（YYYY-MM）")
]
LimitOption = Annotated[
    int | None,
    typer.Option("--limit", help="処理件数の上限。開発中の高速反復に使う"),
]
DryRunOption = Annotated[
    bool, typer.Option("--dry-run", help="出力を書かずに件数だけ確認する")
]


def _echo_summary(result: dict) -> None:
    """manifest の要点を標準出力に出す。コンソールはログをそのまま流す。"""
    counts = result.get("counts", {})
    typer.echo(
        f"[{result.get('phase')}] status={result.get('status')} "
        f"input={counts.get('input')} success={counts.get('success')} "
        f"failed={counts.get('failed')} skipped={counts.get('skipped')} "
        f"({result.get('duration_sec')}s)"
    )
    for w in result.get("warnings", []):
        typer.secho(f"  ⚠ {w['code']} x{w['count']}: {w.get('message') or ''}", fg="yellow")
    for out in result.get("outputs", []):
        typer.echo(f"  → {out['path']} ({out.get('records')} records)")


@app.callback(invoke_without_command=True)
def _root(
    version: Annotated[
        bool, typer.Option("--version", help="バージョンを表示して終了する")
    ] = False,
) -> None:
    if version:
        typer.echo(f"mailauth {__version__}")
        raise typer.Exit()


@app.command("p1-population")
def p1_population(
    config: Annotated[
        str, typer.Option("--config", help="母集団設定 YAML のパス")
    ] = "configs/populations/jp-prime.yaml",
    run: RunOption = "",
    limit: LimitOption = None,
    source_file: Annotated[
        Path | None,
        typer.Option(
            "--source-file",
            help="EDINETコードリストのローカルファイル。指定するとネットワークに出ない",
        ),
    ] = None,
    dry_run: DryRunOption = False,
) -> None:
    """P1 母集団確定 ── 企業リストの取得と identity 付与。"""
    from .p1_population import PopulationNotImplementedError
    from .p1_population import run as run_p1

    run_id = validate_run_id(run or default_run_id())
    try:
        result = run_p1(
            config=config, run_id=run_id, limit=limit, source_file=source_file, dry_run=dry_run
        )
    except PopulationNotImplementedError as exc:
        typer.secho(f"未実装: {exc}", fg="red", err=True)
        raise typer.Exit(code=2) from exc
    _echo_summary(result)
    if result.get("status") == "failed":
        raise typer.Exit(code=1)


def _stub_command(phase: str):
    def command(run: RunOption = "") -> None:
        run_id = validate_run_id(run or default_run_id())
        try:
            not_implemented(phase, run_id)
        except PhaseNotImplementedError as exc:
            typer.secho(str(exc), fg="red", err=True)
            raise typer.Exit(code=2) from exc

    command.__name__ = phase
    return command


for _phase in PHASES[1:]:
    # p4_measure -> p4-measure
    _cmd_name = _phase.replace("_", "-", 1).replace("_", "-")
    app.command(_cmd_name, help=f"{_phase.upper()} {PHASE_LABELS[_phase]} ── 未実装")(
        _stub_command(_phase)
    )


@app.command("status")
def status(
    run: RunOption = "",
    as_json: Annotated[bool, typer.Option("--json", help="JSON で出力する")] = False,
) -> None:
    """指定 run の各フェーズの manifest を要約する。"""
    from .manifest import read_manifest

    run_id = validate_run_id(run or default_run_id())
    rows = []
    for phase in PHASES:
        m = read_manifest(phase_dir(run_id, phase))
        rows.append(
            {
                "phase": phase,
                "label": PHASE_LABELS[phase],
                "status": (m or {}).get("status", "not_run"),
                "counts": (m or {}).get("counts", {}),
                "duration_sec": (m or {}).get("duration_sec"),
            }
        )

    if as_json:
        typer.echo(json.dumps({"run_id": run_id, "phases": rows}, ensure_ascii=False, indent=2))
        return

    typer.echo(f"run {run_id}")
    for r in rows:
        c = r["counts"]
        counts = (
            f"in={c.get('input', '-')} ok={c.get('success', '-')} "
            f"ng={c.get('failed', '-')} skip={c.get('skipped', '-')}"
            if c
            else ""
        )
        typer.echo(f"  {r['phase']:<16} {r['status']:<10} {counts}")


@app.command("view")
def view(
    run: RunOption = "",
    view_id: Annotated[
        str, typer.Option("--view", help="ビューID。configs/views/ の定義")
    ] = "jp-all",
    by: Annotated[
        str | None,
        typer.Option("--by", help="集計軸。common12 | industry | segment | country | status"),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="JSON で出力する")] = False,
) -> None:
    """計測済みの run を別の軸で見る。

    計測は全上場で一度だけ回し、見るときに絞る。

        mailauth view --run 2026-08 --view jp-all   --by common12
        mailauth view --run 2026-08 --view jp-prime --by common12
        mailauth view --run 2026-08 --view jp-all   --by segment
    """
    from .io import read_parquet
    from .paths import phase_output
    from .views import get_view, summarize

    run_id = validate_run_id(run or default_run_id())
    df = read_parquet(phase_output(run_id, "p1_population", "entities.parquet"))
    if df is None:
        typer.secho(
            f"run {run_id} の entities.parquet がありません。先に p1-population を実行してください",
            fg="red",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        cfg = get_view(view_id)
    except KeyError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(code=2) from exc

    result = summarize(df, cfg, group_by=by)

    if as_json:
        typer.echo(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return

    typer.echo(f"{result.label}（{result.view_id}） run={run_id} 軸={result.group_by}")
    for w in result.warnings:
        typer.secho(f"  ⚠ {w}", fg="yellow")
    if not result.groups:
        typer.echo("  該当なし")
    for g in result.groups:
        bar = "▓" * max(1, round(g["share"] * 40)) if g["n"] else ""
        typer.echo(f"  {g['label']:<20} {g['n']:>6}  {g['share']:>6.1%} {bar}")
    typer.echo(f"  {'合計':<20} {result.total:>6}")
    cov = result.coverage
    typer.echo(
        f"  （run全体 {cov['entities_in_run']} 社 / うち本ビュー {cov['entities_in_view']} 社"
        f" / 区分付き {cov['with_segment']} 社）"
    )


@app.command("views")
def views() -> None:
    """使えるビューの一覧。"""
    from .views import list_views

    for cfg in list_views():
        need = "（要 市場区分データ）" if cfg.requires_segment else ""
        typer.echo(f"{cfg.id:<14} {cfg.label}{need}")


@app.command("runs")
def runs() -> None:
    """存在する run の一覧。"""
    ids = list_run_ids()
    if not ids:
        typer.echo("run がまだありません")
        return
    for run_id in ids:
        typer.echo(run_id)


@app.command("populations")
def populations() -> None:
    """母集団プリセットの一覧と実装状況。"""
    for cfg in list_populations():
        state = "実装済" if cfg.implemented and cfg.enabled else "未実装/無効"
        note = f" ({cfg.blocked_by})" if cfg.blocked_by else ""
        typer.echo(f"{cfg.id:<16} {cfg.country:<6} {state:<12} {cfg.label}{note}")


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        typer.secho("中断しました", fg="yellow", err=True)
        sys.exit(130)


if __name__ == "__main__":
    main()
