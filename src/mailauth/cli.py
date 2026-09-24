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
    help="Email DNS Monitor ── メール認証の月次観測。設計仕様は DESIGN.md を参照。",
    no_args_is_help=True,
    add_completion=False,
)

RunOption = Annotated[str, typer.Option("--run", help="実行ID。既定は当月（YYYY-MM）")]
LimitOption = Annotated[
    int | None,
    typer.Option("--limit", help="処理件数の上限。開発中の高速反復に使う"),
]
DryRunOption = Annotated[bool, typer.Option("--dry-run", help="出力を書かずに件数だけ確認する")]


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
    offline: Annotated[
        bool,
        typer.Option(
            "--offline",
            help="外部 API に問い合わせない。補完しなかったことは manifest に残る",
        ),
    ] = False,
) -> None:
    """P1 母集団確定 ── 企業リストの取得と identity 付与。"""
    from .p1_population import PopulationNotImplementedError
    from .p1_population import run as run_p1

    run_id = validate_run_id(run or default_run_id())
    try:
        result = run_p1(
            config=config,
            run_id=run_id,
            limit=limit,
            source_file=source_file,
            dry_run=dry_run,
            offline=offline,
        )
    except PopulationNotImplementedError as exc:
        typer.secho(f"未実装: {exc}", fg="red", err=True)
        raise typer.Exit(code=2) from exc
    _echo_summary(result)
    if result.get("status") == "failed":
        raise typer.Exit(code=1)


@app.command("p2-candidates")
def p2_candidates(
    run: RunOption = "",
    limit: LimitOption = None,
    dry_run: DryRunOption = False,
    config: Annotated[
        str, typer.Option("--config", help="候補生成のパラメータ")
    ] = "configs/candidates.yaml",
) -> None:
    """P2 ドメイン候補生成 ── 企業から関連ドメイン群を展開（再現率優先）。"""
    from .p2_candidates import MissingInputError
    from .p2_candidates import run as run_p2

    run_id = validate_run_id(run or default_run_id())
    try:
        result = run_p2(run_id=run_id, limit=limit, dry_run=dry_run, config=config)
    except MissingInputError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(code=2) from exc
    _echo_summary(result)
    if result.get("status") == "failed":
        raise typer.Exit(code=1)


@app.command("p3-domains")
def p3_domains(
    run: RunOption = "",
    limit: LimitOption = None,
    dry_run: DryRunOption = False,
    config: Annotated[
        str, typer.Option("--config", help="確度判定のパラメータ")
    ] = "configs/candidates.yaml",
) -> None:
    """P3 メールドメイン確定 ── 候補を絞り三段の確度フラグを付ける。"""
    from .p3_domains import MissingInputError
    from .p3_domains import run as run_p3

    run_id = validate_run_id(run or default_run_id())
    try:
        result = run_p3(run_id=run_id, limit=limit, dry_run=dry_run, config=config)
    except MissingInputError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(code=2) from exc
    _echo_summary(result)
    if result.get("status") == "failed":
        raise typer.Exit(code=1)


@app.command("p4-measure")
def p4_measure(
    run: RunOption = "",
    method: Annotated[
        str, typer.Option("--method", help="計測バックエンド。dnspython | zdns")
    ] = "dnspython",
    tier: Annotated[
        str | None, typer.Option("--tier", help="階層を絞る。A（フル）| C（簡易）")
    ] = None,
    limit: LimitOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """P4 DNS計測 ── メール認証レコードを取得し bronze に生保存。"""
    from .p4_measure import MissingInputError, UnknownBackendError
    from .p4_measure import run as run_p4

    run_id = validate_run_id(run or default_run_id())
    try:
        result = run_p4(run_id=run_id, method=method, tier=tier, limit=limit, dry_run=dry_run)
    except (MissingInputError, UnknownBackendError) as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(code=2) from exc
    _echo_summary(result)
    if result.get("status") == "failed":
        raise typer.Exit(code=1)


@app.command("p4-compare")
def p4_compare(
    run: RunOption = "",
    methods: Annotated[
        str | None,
        typer.Option("--methods", help="比べる手法。既定は bronze にある全部"),
    ] = None,
    resolvers: Annotated[
        str | None,
        typer.Option(
            "--resolvers",
            help="リゾルバ間のクロスチェック。指定すると抽出分だけ DNS に出る",
        ),
    ] = None,
    sample: Annotated[
        float | None,
        typer.Option("--sample", help="クロスチェックの抽出率。既定は measure.yaml"),
    ] = None,
    limit: LimitOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """P4 手法比較 ── 複数手法・複数リゾルバの観測差分を出す。"""
    from .p4_measure import MissingInputError, UnknownBackendError
    from .p4_measure.compare_runner import run as run_compare

    run_id = validate_run_id(run or default_run_id())
    try:
        result = run_compare(
            run_id=run_id,
            methods=[m.strip() for m in methods.split(",")] if methods else None,
            resolvers=[r.strip() for r in resolvers.split(",")] if resolvers else None,
            sample_rate=sample,
            limit=limit,
            dry_run=dry_run,
        )
    except (MissingInputError, UnknownBackendError) as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(code=2) from exc
    _echo_summary(result)
    if result.get("status") == "failed":
        raise typer.Exit(code=1)


@app.command("p5-parse")
def p5_parse(
    run: RunOption = "",
    limit: LimitOption = None,
    dry_run: DryRunOption = False,
    no_dns: Annotated[
        bool,
        typer.Option(
            "--no-dns",
            help="Tree Walk と rua 宛先検証を行わない（DNS に出ない）",
        ),
    ] = False,
) -> None:
    """P5 パース ── bronze を構造化し仕様に照らして解釈する。"""
    from .config import load_measure_config
    from .p5_parse import MissingInputError
    from .p5_parse import run as run_p5
    from .resolver import DnsResolver

    run_id = validate_run_id(run or default_run_id())
    resolver = None
    if not no_dns:
        rate = load_measure_config().get("rate", {})
        resolver = DnsResolver(
            timeout=float(rate.get("timeout_sec", 5)),
            retries=int(rate.get("retries", 3)),
            qps=float(rate.get("qps", 20)),
        )
    try:
        result = run_p5(run_id=run_id, limit=limit, dry_run=dry_run, resolver=resolver)
    except MissingInputError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(code=2) from exc
    _echo_summary(result)
    if result.get("status") == "failed":
        raise typer.Exit(code=1)


@app.command("p6-infer")
def p6_infer(
    run: RunOption = "",
    limit: LimitOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """P6 推察 ── fact からメール基盤とセキュリティ製品を推定する。"""
    from .p6_infer import MissingInputError
    from .p6_infer import run as run_p6
    from .p6_infer.fingerprints import FingerprintError

    run_id = validate_run_id(run or default_run_id())
    try:
        result = run_p6(run_id=run_id, limit=limit, dry_run=dry_run)
    except (MissingInputError, FingerprintError) as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(code=2) from exc
    _echo_summary(result)
    if result.get("status") == "failed":
        raise typer.Exit(code=1)


@app.command("p7-aggregate")
def p7_aggregate(
    run: RunOption = "",
    dry_run: DryRunOption = False,
    min_cell_size: Annotated[
        int | None,
        typer.Option(
            "--min-cell-size",
            help="業種別集計のセル秘匿の閾値。既定は5（k-匿名性の実務値）",
        ),
    ] = None,
) -> None:
    """P7 集計 ── 公開用の統計を gold に生成し前月差分を計算する。"""
    from .p7_aggregate import MissingInputError
    from .p7_aggregate import run as run_p7

    run_id = validate_run_id(run or default_run_id())
    try:
        result = run_p7(run_id=run_id, dry_run=dry_run, threshold=min_cell_size)
    except MissingInputError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(code=2) from exc
    _echo_summary(result)
    if result.get("status") == "failed":
        raise typer.Exit(code=1)


@app.command("p8-publish")
def p8_publish(
    run: RunOption = "",
    dry_run: DryRunOption = False,
    config: Annotated[str, typer.Option("--config", help="公開の設定")] = "configs/publish.yaml",
    any_month: Annotated[
        bool,
        typer.Option(
            "--any-month",
            help="gold にある月をそのまま出す。指定月が無くてもエラーにしない（CI 用）",
        ),
    ] = False,
) -> None:
    """P8 公開 ── gold を公開サイトのデータに書き出し表現規約を検査する。"""
    from .p8_publish import MissingInputError, PublishBlockedError
    from .p8_publish import run as run_p8

    run_id = validate_run_id(run or default_run_id())
    try:
        result = run_p8(run_id=run_id, dry_run=dry_run, config=config, require_month=not any_month)
    except MissingInputError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(code=2) from exc
    except PublishBlockedError as exc:
        # 公開してはいけないものが混ざっている。警告では済ませない
        typer.secho(f"公開を中止しました: {exc}", fg="red", err=True)
        raise typer.Exit(code=3) from exc
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


#: 実装済みのフェーズ。残りはスタブとして登録する
IMPLEMENTED = {
    "p1_population",
    "p2_candidates",
    "p3_domains",
    "p4_measure",
    "p5_parse",
    "p6_infer",
    "p7_aggregate",
    "p8_publish",
}

for _phase in PHASES:
    if _phase in IMPLEMENTED:
        continue
    # p4_measure -> p4-measure
    _cmd_name = _phase.replace("_", "-", 1).replace("_", "-")
    app.command(_cmd_name, help=f"{_phase.upper()} {PHASE_LABELS[_phase]} ── 未実装")(
        _stub_command(_phase)
    )


@app.command("run-report")
def run_report(
    run: RunOption = "",
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Markdown の書き出し先。省略すると標準出力"),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="JSON で出す")] = False,
    fail_on_error: Annotated[
        bool,
        typer.Option(
            "--fail-on-error",
            help="失敗した工程があれば終了コード1。未実行は失敗にしない",
        ),
    ] = False,
) -> None:
    """八工程の manifest を1つにまとめる（月次実行の記録）。"""
    from .report import NOT_RUN, collect, exists, to_markdown

    run_id = validate_run_id(run or default_run_id())
    if not exists(run_id):
        typer.secho(f"run {run_id} がありません", fg="red", err=True)
        raise typer.Exit(code=2)

    report = collect(run_id)
    if as_json:
        typer.echo(json.dumps(report.to_dict(), ensure_ascii=False, indent=1))
    else:
        text = to_markdown(report)
        if out is None:
            typer.echo(text)
        else:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")
            typer.echo(f"→ {out}")

    if report.status == NOT_RUN:
        typer.secho("どの工程も実行されていません", fg="yellow", err=True)
    for phase in report.failed_phases:
        typer.secho(f"  ✗ {phase.phase}: {phase.error or '理由の記録なし'}", fg="red")

    if fail_on_error and report.failed_phases:
        raise typer.Exit(code=1)


#: 英語ページの前書き。**フッタの限界の明示は言語ごとに要る**
#: （設定ファイルのフッタは日本語なので、英語ページは自分で持つ）。
_EN_FRONT_MATTER = (
    "---\n"
    "title: Change log\n"
    "footer: This site measures conformance to published standards and is not "
    'an overall security assessment. Data is <a href="/en/terms">CC0</a>.\n'
    "---\n\n"
)


@app.command("changelog")
def changelog_cmd(
    out: Annotated[
        Path | None,
        typer.Option("--out", help="書き出し先。既定は site/src/changelog.md"),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="JSON で出す")] = False,
    stdout: Annotated[bool, typer.Option("--stdout", help="ファイルに書かず標準出力へ")] = False,
) -> None:
    """変更履歴を gold と manifest から生成する。

    版が上がったことは機械が検出するが、**それが数字にどう影響したかは
    人が書く。** 生成物には解釈の欄が空いたまま出る。
    """
    from .changelog import available_months, build, to_json, to_markdown
    from .paths import config_path

    months = available_months()
    if not months:
        typer.secho(
            "gold がまだありません。先に p7-aggregate を実行してください",
            fg="yellow",
            err=True,
        )
    entries = build(months)

    if as_json:
        typer.echo(to_json(entries))
        return

    if stdout:
        typer.echo(to_markdown(entries))
        return

    target = out or config_path("site/src/changelog.md")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(to_markdown(entries), encoding="utf-8")
    typer.echo(f"→ {target}（{len(entries)} か月分）")

    # **英語版も同時に出す。** 変更履歴が片方の言語にしか無いと、
    # もう片方で読んだ人は「数字が動いた理由」を確かめる場所を持たない。
    # `--out` を指定した場合は書かない（出力先が1つ指定されているため）
    if out is None:
        en_target = config_path("site/src/en/changelog.md")
        en_target.parent.mkdir(parents=True, exist_ok=True)
        en_target.write_text(
            _EN_FRONT_MATTER + to_markdown(entries, lang="en"), encoding="utf-8"
        )
        typer.echo(f"→ {en_target}")

    pending = [e.month for e in entries if e.has_measurement_change]
    if pending:
        typer.secho(
            f"  ⚠ 計測側の変更があった月: {', '.join(pending)}。「解釈」の欄を人が埋めること",
            fg="yellow",
        )


@app.command("worklist")
def worklist_cmd(
    run: RunOption = "",
    out: Annotated[
        Path | None,
        typer.Option("--out", help="書き出し先。既定は runs/<run_id>-worklist.md"),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="JSON で出す")] = False,
    stdout: Annotated[bool, typer.Option("--stdout", help="ファイルに書かず標準出力へ")] = False,
) -> None:
    """未知 MX ホストの月次作業リストを作る。

    P6 は未知ホストを頻度順で manifest に出しているが、**一覧が出るだけでは
    routine にならない。** 毎月同じ顔ぶれが並んでいても気付けない。

    ここでは月をまたいで「何か月連続で未知のままか」を数え、調査して同定
    できなかったホストを未着手と区別する。
    """
    import json as _json

    from . import worklist as mod
    from .paths import repo_root

    run_id = validate_run_id(run or default_run_id())
    result = mod.build(run_id)

    if as_json:
        typer.echo(_json.dumps(result.to_dict(), ensure_ascii=False, indent=1))
        return

    text = mod.to_markdown(result)
    if stdout:
        typer.echo(text)
    else:
        target = out or repo_root() / "runs" / f"{run_id}-worklist.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        typer.echo(
            f"→ {target}（手を付けるもの {len(result.hosts)} 件 / "
            f"調査済み {len(result.set_aside)} 件）"
        )

    if result.stale:
        typer.secho(
            f"  ⚠ {mod.STALE_MONTHS} か月以上そのままのホストが "
            f"{len(result.stale)} 件: " + ", ".join(h.registered_domain for h in result.stale[:5]),
            fg="yellow",
        )
    if result.truncated:
        typer.secho(
            f"  ⚠ P6 の未知ホスト一覧が上限 {mod.TOP_N} 件に達している。"
            "一覧に無いホストが残っている",
            fg="yellow",
        )
    if not result.unidentified_available:
        typer.secho(
            "  ⚠ 調査済みの記録を読めていないため、すべて未着手として並べた",
            fg="yellow",
        )


@app.command("corrections")
def corrections_cmd(
    out: Annotated[
        Path | None,
        typer.Option("--out", help="書き出し先。既定は runs/corrections-log.md"),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="JSON で出す")] = False,
    stdout: Annotated[bool, typer.Option("--stdout", help="ファイルに書かず標準出力へ")] = False,
    fail_on_overdue: Annotated[
        bool,
        typer.Option(
            "--fail-on-overdue",
            help="審査の上限を超えた申告があれば exit code 1 で終わる",
        ),
    ] = False,
) -> None:
    """訂正申告の登録簿から訂正履歴を書き出す。

    **公開サイトには出さない。** 本システムは内部運用で、外部からの訂正申告を
    受け付けていない。登録簿と審査期限の仕組みは残してある ── 第2層（個社名
    付き明細）を外部に出す段になれば、訂正窓口の常設は名誉毀損の抗弁における
    公益目的の立証材料になるため（DESIGN.md 1.4）。そのとき公開先を戻す。

    **審査中のものも、訂正しなかったものも載せる。** 訂正した分だけを載せると、
    申告が何件あってどう扱われたのかが読み手に分からない。

    審査は受付から48時間以内を目標、72時間を上限とする（DESIGN.md Sprint 10）。
    超過を機械的に検出する。**書いただけでは守られない。**
    """
    from . import corrections as mod
    from .paths import config_path

    registry = mod.load()

    if as_json:
        typer.echo(mod.to_json(registry))
        return

    text = mod.to_markdown(registry)
    if stdout:
        typer.echo(text)
    else:
        target = out or config_path("runs/corrections-log.md")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        typer.echo(f"→ {target}（{len(registry.entries)} 件）")

    if not registry.available:
        # **「読めなかった」を「申告0件」として通さない**
        typer.secho("  ⚠ 登録簿を読めていない。**「申告0件」ではない**", fg="red", err=True)
        for problem in registry.problems + registry.notes:
            typer.secho(f"    - {problem}", fg="red", err=True)
        raise typer.Exit(code=2)

    open_entries = registry.open_entries()
    if open_entries:
        typer.echo(f"  審査中/受付: {', '.join(e.id for e in open_entries)}")
    overdue = registry.overdue()
    if overdue:
        typer.secho(
            f"  ⚠ 審査の上限（{mod.SLA_LIMIT_HOURS} 時間）を超過: "
            f"{', '.join(e.id for e in overdue)}",
            fg="red",
        )
        if fail_on_overdue:
            raise typer.Exit(code=1)


@app.command("notify-plan")
def notify_plan_cmd(
    run: RunOption = "",
    config: Annotated[
        str, typer.Option("--config", help="通知の設定（publish.yaml の notify 節）")
    ] = "configs/publish.yaml",
    limit: Annotated[int | None, typer.Option("--limit", help="先頭 N 件だけを計画にする")] = None,
    only: Annotated[str, typer.Option("--only", help="対象ドメインをカンマ区切りで絞る")] = "",
    no_https: Annotated[
        bool,
        typer.Option(
            "--no-https",
            help="security.txt を取りに HTTPS を叩かない（「見ていない」と記録する）",
        ),
    ] = False,
    stdout: Annotated[bool, typer.Option("--stdout", help="ファイルに書かず標準出力へ")] = False,
) -> None:
    """事前通知の計画を作る。**メールは送らない。**

    送信の可否は運用上の判断であり、コードが勝手に始めてよいものではない。
    このコマンドが作るのは「誰にどの文面を送るか」の一覧までである。

    作る前に4つの門を通る。どれも警告ではなく停止である。
    送信元自身の認証が完全準拠か / オプトアウト登録簿が読めているか /
    訂正窓口があるか / 文面が語彙規約と営業要素の検査を通るか。
    """
    from .p9_notify import MissingInputError, PlanBlockedError, SelfComplianceError
    from .p9_notify import run as run_p9

    run_id = validate_run_id(run or default_run_id())
    domains = [d.strip() for d in only.split(",") if d.strip()]
    try:
        plan, written = run_p9(
            run_id,
            config=config,
            limit=limit,
            only=domains or None,
            fetch_https=False if no_https else None,
            write=not stdout,
        )
    except MissingInputError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(code=2) from exc
    except SelfComplianceError as exc:
        # **自分の設定が不備な状態では通知を作らない**
        typer.secho(f"通知計画を作りませんでした:\n{exc}", fg="red", err=True)
        raise typer.Exit(code=3) from exc
    except PlanBlockedError as exc:
        typer.secho(f"通知計画を作りませんでした: {exc}", fg="red", err=True)
        raise typer.Exit(code=3) from exc

    if stdout:
        typer.echo(plan.to_markdown())
        return

    typer.echo(
        f"[notify-plan] 対象 {plan.count} 件 / 対象外 {len(plan.skipped)} 件 "
        f"（毎秒{plan.rate_per_sec:g}通で約{plan.duration_sec / 60:.0f}分）"
    )
    for path in written.get("paths", []):
        typer.echo(f"  → {path}")
    typer.secho(
        "  ⚠ このコマンドは送信していません。"
        f"第2層を出せる最短日は {plan.earliest_publish.isoformat()} です",
        fg="yellow",
    )


@app.command("offload")
def offload_cmd(
    run: RunOption = "",
    dry_run: DryRunOption = False,
) -> None:
    """bronze / silver を R2 に退避する（Git には入れない層）。"""
    from .offload import OffloadError, offload

    run_id = validate_run_id(run or default_run_id())
    try:
        plan, result = offload(run_id, dry_run=dry_run)
    except OffloadError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(
        f"[offload] 対象 {plan.count} 件 / {plan.total_bytes} バイト → "
        f"送信 {result.uploaded} 件 失敗 {result.failed} 件"
    )
    # **宛先ごとに分けて出す。** 片方だけ失敗したときにどちらを再実行すれば
    # よいか分からないと、再実行が全宛先へのやり直しになる
    for name, sent in sorted(result.by_destination.items()):
        typer.echo(
            f"  {name}: 送信 {sent.uploaded} 件 失敗 {sent.failed} 件"
            + (f"（{sent.reason}）" if sent.reason else "")
        )
    if result.reason:
        typer.secho(f"  ⚠ {result.reason}", fg="yellow")
    for note in result.notes:
        # 持続性の基準を満たしていないことは、送信が成功していても言う
        typer.secho(f"  ⚠ {note}", fg="yellow")
    for error in result.errors:
        typer.secho(f"  ✗ {error}", fg="red", err=True)
    if result.failed:
        raise typer.Exit(code=1)


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


@app.command("access-check")
def access_check_cmd(
    as_json: Annotated[bool, typer.Option("--json", help="機械可読で出す")] = False,
) -> None:
    """アクセス制御が**実際に**掛かっているかを外から確かめる。

    `configs/publish.yaml` の `access.protected_paths` を認証なしで叩き、
    弾かれることを確認する。設定ファイルの申告は証拠にならないので、
    第2層を出す実行では P8 がこれと同じ検査を必ず通す。
    """
    from . import access as access_mod

    cfg = access_mod.load()
    report = access_mod.verify(cfg)

    if as_json:
        typer.echo(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        raise typer.Exit(0 if report.verified else 1)

    if cfg.idp:
        typer.echo(f"ID プロバイダ: {cfg.idp}")
    if cfg.allowed_email_domains:
        typer.echo("通すメールドメイン: " + ", ".join(cfg.allowed_email_domains))
        typer.echo(
            "  ※ この値は記録であって検査には使えない。"
            "Cloudflare Access 側にメールドメインの条件を置くこと"
        )
    typer.echo("")

    for p in report.probes:
        mark = {"protected": "○", "open": "×", "unknown": "?"}.get(p.state, "?")
        typer.secho(
            f"  {mark} {p.url}  {p.detail}",
            fg={"protected": "green", "open": "red"}.get(p.state, "yellow"),
        )
    if not report.probes:
        typer.echo("  （検査できなかった）")
    typer.echo("")

    if report.verified:
        typer.secho("すべての対象パスが認証に弾かれた", fg="green")
        tier2_path = (access_mod.load_yaml("configs/publish.yaml").get("deploy") or {}).get(
            "tier2_path"
        )
        if access_mod.tier1_is_gated(report, tier2_path):
            typer.secho(
                "⚠ 第1層まで認証の内側にある。試験中なら妥当だが、公開前に外すこと",
                fg="yellow",
            )
        raise typer.Exit(0)

    for reason in report.reasons():
        typer.secho(f"  ✗ {reason}", fg="red", err=True)
    raise typer.Exit(1)


@app.command("noindex-check")
def noindex_check_cmd(
    paths: Annotated[
        str,
        typer.Option("--paths", help="確かめるパス。カンマ区切り"),
    ] = "/,/data/stats_overall.json",
) -> None:
    """検索エンジンに載らない設定が**実際に配信されているか**を確かめる。

    設定ファイルに書いたことと、配信されているものは別である。
    `X-Robots-Tag` を優先して見る ── meta robots は HTML にしか効かず、
    公開データ（JSON / CSV）を直接リンクされた場合に届かない。
    """
    from . import access as access_mod

    cfg = access_mod.load()
    if not cfg.verify_base_url:
        typer.secho(
            "公開サイトの URL が未設定。環境変数 MAILAUTH_SITE_BASE_URL に入れること"
            "（configs/publish.yaml の access.verify_base_url でも読むが、"
            "**リポジトリを public にするなら住所を書き残さない方がよい**）",
            fg="red",
            err=True,
        )
        raise typer.Exit(2)

    base = cfg.verify_base_url.rstrip("/")
    results = []
    for path in [p.strip() for p in paths.split(",") if p.strip()]:
        r = access_mod.probe_noindex(base + "/" + path.lstrip("/"))
        results.append(r)
        mark = {"protected": "○", "open": "×", "unknown": "?"}.get(r.state, "?")
        typer.secho(
            f"  {mark} {r.url}  {r.detail}",
            fg={"protected": "green", "open": "red"}.get(r.state, "yellow"),
        )

    # **「載りうる」と「確かめられなかった」を混ぜない**（原則5）。
    # デプロイ前はすべて unknown になるが、それは不備ではない。
    # ここで「効いていない」と言うと、直すものが無いのに直そうとする
    exposed = [r for r in results if r.state == access_mod.OPEN]
    unknown = [r for r in results if r.state == access_mod.UNKNOWN]

    if exposed:
        typer.secho(
            "\n検索避けが効いていない経路がある。"
            "site/static/_headers が dist/ に配られているか確認すること",
            fg="red",
            err=True,
        )
        raise typer.Exit(1)
    if unknown:
        typer.secho(
            "\n確かめられなかった経路がある。**効いていないとは限らない。**"
            "デプロイ前ならこれが正常で、出てから実行し直すこと",
            fg="yellow",
            err=True,
        )
        raise typer.Exit(2)
    typer.secho("\nすべての経路に X-Robots-Tag が付いている", fg="green")


@app.command("doctor")
def doctor_cmd(
    as_json: Annotated[bool, typer.Option("--json", help="機械可読で出す（コンソール用）")] = False,
) -> None:
    """いま何をすればいいかを1つだけ出す。

    運営者の作業は9件あるが依存関係があり、実際に着手できるのは常に1〜2件で
    ある。**全部を並べると全部が未完了に見える**ので、持っている鍵と生成物の
    状態から次の一手を1つに絞って出す。読むだけで何も書き換えない。
    """
    from .doctor import diagnose, render

    report = diagnose()
    if as_json:
        typer.echo(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return
    typer.echo(render(report))


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        typer.secho("中断しました", fg="yellow", err=True)
        sys.exit(130)


if __name__ == "__main__":
    main()
