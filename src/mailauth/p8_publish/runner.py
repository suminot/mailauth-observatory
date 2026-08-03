"""P8 公開の本体。

gold を公開サイトのデータディレクトリに書き出し、表現上の規約を検査する。
Cloudflare Pages へのデプロイ自体は CI か手元の `wrangler` が行う。
このフェーズの責務は「**出してよいものだけを出す**」ことである。

3つの門を通す。どれも警告ではなく停止させる。
  1. 第1層に個社特定情報が混ざっていないか（列名で機械的に弾く）
  2. 公開ページに断定的な語彙や順位付けが無いか
  3. 限界の明示が常時表示の位置にあるか

第2層は既定で出さない。訂正期間の日数判定はコードで行い、記憶に頼らない。
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

from ..config import load_yaml
from ..io import read_parquet
from ..manifest import RunManifest
from ..p7_aggregate import BY_SECTOR_FILENAME, OVERALL_FILENAME
from ..paths import config_path, gold_dir, gold_root, phase_dir
from . import tiers, vocabulary

PHASE = "p8_publish"
PUBLISHER_VERSION = "1.0.0"
DEFAULT_CONFIG = "configs/publish.yaml"

MONTH_DIR_RE = re.compile(r"^month=(\d{4}-\d{2})$")

#: サイトのページを探す場所。ここの .md を語彙検査にかける
PAGE_GLOB = "src/**/*.md"


class MissingInputError(RuntimeError):
    pass


class PublishBlockedError(RuntimeError):
    """公開してはいけないものが混ざっている。**警告ではなく停止させる。**"""


def available_months() -> list[str]:
    root = gold_root()
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir()):
        m = MONTH_DIR_RE.match(d.name)
        if d.is_dir() and m:
            out.append(m.group(1))
    return out


def _jsonable(value: Any) -> Any:
    import pandas as pd

    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if hasattr(value, "tolist") and not isinstance(value, str):
        return value.tolist()
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return value


def frame_to_records(frame) -> list[dict]:
    return [
        {k: _jsonable(v) for k, v in row.items()}
        for row in frame.to_dict(orient="records")
    ]


def collect_months(months: list[str]) -> tuple[list[dict], list[dict]]:
    """全月分の gold を縦に積む。時系列グラフの元になる。"""
    overall: list[dict] = []
    sectors: list[dict] = []
    for month in months:
        o = read_parquet(gold_dir(month) / OVERALL_FILENAME)
        if o is not None:
            overall.extend(frame_to_records(o))
        s = read_parquet(gold_dir(month) / BY_SECTOR_FILENAME)
        if s is not None:
            sectors.extend(frame_to_records(s))
    return overall, sectors


def lint_pages(site_dir: Path) -> tuple[list[str], list[str]]:
    """公開ページの語彙を検査する。

    戻り値は (違反の説明, 検査したファイル)。
    """
    problems: list[str] = []
    checked: list[str] = []
    for path in sorted(site_dir.glob(PAGE_GLOB)):
        if "node_modules" in path.parts or ".observablehq" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        checked.append(path.name)
        for v in vocabulary.check(text):
            problems.append(f"{path.name}: 「{v.term}」── {v.advice}（…{v.excerpt}…）")
    return problems, checked


def run(
    run_id: str,
    *,
    dry_run: bool = False,
    config: str = DEFAULT_CONFIG,
    today: dt.date | None = None,
    require_month: bool = True,
) -> dict[str, Any]:
    """P8 を実行し manifest の内容を返す。

    `require_month=False` は「gold にある月をそのまま出す」動作で、CI から
    使う。特定の月を要求していないので、その月が無いことはエラーにしない。
    """
    out_dir = phase_dir(run_id, PHASE)
    cfg = load_yaml(config)
    site_cfg = cfg.get("site") or {}
    tier2_cfg = cfg.get("tier2") or {}
    now = today or dt.date.today()

    site_dir = config_path(str(site_cfg.get("dir") or "site"))
    data_dir = config_path(str(site_cfg.get("data_dir") or "site/src/data"))

    with RunManifest(
        run_id=run_id,
        phase=PHASE,
        out_dir=out_dir,
        tool_versions={"publisher": PUBLISHER_VERSION},
        params={"dry_run": dry_run, "config": config},
    ) as manifest:
        months = available_months()
        if require_month and run_id[:7] not in months:
            raise MissingInputError(
                f"{gold_dir(run_id[:7])} がありません。先に p7-aggregate を実行してください"
            )
        if not months:
            # gold がまだ無い状態でもサイトは組める。**空の数字を出すのではなく、
            # データが無いことをサイト側に伝える。** 0% と「未計測」は別物
            manifest.add_warning(
                "NO_GOLD",
                message=(
                    "gold がまだ無いため、データの無いサイトを組んだ。"
                    "p7-aggregate を実行すると数字が入る"
                ),
            )

        overall, sectors = collect_months(months)
        manifest.counts.input = len(overall) + len(sectors)

        # -- 門1: 第1層に個社特定情報が混ざっていないか ----------------------
        for name, records in (("stats_overall", overall), ("stats_by_sector", sectors)):
            if not records:
                continue
            bad = tiers.tier1_violations(list(records[0]))
            if bad:
                raise PublishBlockedError(
                    f"{name} に第1層で公開できない列がある: {bad}。"
                    "個社特定情報は第2層（認証の内側）にしか置けない"
                )

        # -- 門2: 表現上の規約 -----------------------------------------------
        problems: list[str] = []
        pages: list[str] = []
        if site_dir.is_dir():
            problems, pages = lint_pages(site_dir)
        else:
            manifest.add_warning(
                "NO_SITE_DIR",
                message=f"{site_dir} が無いためページの語彙検査をしていない",
            )
        if problems:
            raise PublishBlockedError(
                "公開ページに断定的な語彙または順位付けがある。"
                "標準準拠の事実記述に直すこと:\n  " + "\n  ".join(problems)
            )

        # -- 門3: 限界の明示 -------------------------------------------------
        declared = str(site_cfg.get("disclaimer") or "")
        if not vocabulary.has_disclaimer(declared):
            raise PublishBlockedError(
                f"設定の disclaimer が規定の文言と一致しない。"
                f"「{vocabulary.DISCLAIMER}」を常時表示すること"
            )

        # -- 第2層の判定 ------------------------------------------------------
        notified = tier2_cfg.get("notified_on")
        if isinstance(notified, str):
            notified = dt.date.fromisoformat(notified)
        decision = tiers.evaluate_tier2(
            notified_on=notified,
            today=now,
            access_control_configured=bool(
                tier2_cfg.get("access_control_configured")
            ),
        )
        tier2_requested = bool(tier2_cfg.get("enabled"))
        if tier2_requested and not decision.releasable:
            raise PublishBlockedError(
                "第2層（個社名付き明細）の公開条件を満たしていない:\n  "
                + "\n  ".join(decision.reasons)
            )
        if not tier2_requested:
            manifest.add_warning(
                "TIER2_DISABLED",
                message=(
                    "第2層は無効。個社名付き明細は出力していない。"
                    "有効化には事前通知から最低 "
                    f"{tiers.MIN_CORRECTION_DAYS} 日の訂正期間と"
                    "アクセス制御の設定が必要"
                ),
            )
        for warning in decision.warnings:
            manifest.add_warning("TIER2_CORRECTION_PERIOD_SHORT", message=warning)

        manifest.set_breakdown(
            months=months,
            latest_month=months[-1] if months else None,
            overall_rows=len(overall),
            sector_rows=len(sectors),
            pages_checked=pages,
            tier2_enabled=tier2_requested,
            tier2_days_elapsed=decision.days_elapsed,
            formats=list((cfg.get("tier1") or {}).get("formats") or []),
        )
        manifest.attribution = list(cfg.get("attribution") or [])

        if dry_run:
            manifest.add_warning("DRY_RUN", message="dry_run のため出力を書いていない")
        else:
            written = _write_site_data(
                data_dir,
                overall=overall,
                sectors=sectors,
                months=months,
                cfg=cfg,
            )
            manifest.counts.success = sum(n for _, n in written)
            for path, n in written:
                manifest.add_output(str(path), records=n)

    return manifest.to_dict()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 冪等にするためキー順を固定する（原則6）
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_csv(path: Path, records: list[dict]) -> None:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    if not records:
        path.write_text("", encoding="utf-8")
        return
    columns = list(records[0])
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in records:
            writer.writerow({k: _csv_cell(row.get(k)) for k in columns})


def _csv_cell(value: Any) -> Any:
    """CSV に list を入れると Excel で壊れるので JSON 文字列にする。"""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def _write_site_data(
    data_dir: Path,
    *,
    overall: list[dict],
    sectors: list[dict],
    months: list[str],
    cfg: dict,
) -> list[tuple[Path, int]]:
    """サイトのデータディレクトリに書き出す。

    Parquet はコピーではなく gold への参照にする。同じ数字を二重に持つと
    片方だけ古くなる。ダウンロード用のリンクは gold を指す。
    """
    formats = set((cfg.get("tier1") or {}).get("formats") or ["json"])
    written: list[tuple[Path, int]] = []

    if "json" in formats:
        for name, records in (("stats_overall", overall), ("stats_by_sector", sectors)):
            path = data_dir / f"{name}.json"
            _write_json(path, records)
            written.append((path, len(records)))

    if "csv" in formats:
        for name, records in (("stats_overall", overall), ("stats_by_sector", sectors)):
            path = data_dir / f"{name}.csv"
            _write_csv(path, records)
            written.append((path, len(records)))

    meta_path = data_dir / "meta.json"
    _write_json(
        meta_path,
        {
            "months": months,
            "latest_month": months[-1] if months else None,
            "license": (cfg.get("tier1") or {}).get("license"),
            "attribution": list(cfg.get("attribution") or []),
            "disclaimer": (cfg.get("site") or {}).get("disclaimer"),
            # ダウンロードは gold を直接指す。データを二重に持たない
            "parquet_path": "gold/month=<YYYY-MM>/",
            # 検出できない製品があることをサイト側にも渡す
            "detection_limits": [
                "MX を変更せず API / OAuth で連携する製品は DNS に痕跡を残さないため、"
                "検出されなかったことは使っていないことを意味しない",
                "DKIM のセレクタは DNS 上で列挙できないため、既知セレクタで"
                "検出できなかったことは未設定の証明にならない",
                "観測できなかったドメインは率の分母から外している。"
                "「取れなかった」と「無かった」は別の事実として扱う",
            ],
        },
    )
    written.append((meta_path, 1))
    return written
