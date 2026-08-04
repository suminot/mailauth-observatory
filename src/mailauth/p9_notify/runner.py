"""通知計画の生成（DESIGN.md Sprint 9）。

**このフェーズは P1〜P8 の計測パイプラインに含めない。** 計測は毎月自動で
回すが、通知は人が判断して起こす。cron に混ぜると、ある月の朝に誰も
気付かないまま数百通が出る。

やること
  1. silver の facts と P3 のドメイン一覧を突き合わせて通知候補を作る
  2. 送信元自身の準拠を測る（通らなければここで止まる）
  3. オプトアウト登録簿を読む（読めなければ止まる）
  4. 文面を描画して計画を書き出す

**送信は行わない。** 出力は人が読むための Markdown と JSON である。
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..io import read_parquet
from ..paths import config_path, phase_dir, phase_output
from ..resolver import DnsResolver, Resolver
from .contacts import ContactSet, discover
from .optout import load as load_optout
from .plan import NotifyPlan, NotifyTarget, PlanBlockedError, build_plan
from .selfcheck import SelfComplianceResult, check_self
from .template import CORRECTION_DAYS_RECOMMENDED, findings_from_fact

PHASE = "p9_notify"
DEFAULT_CONFIG = "configs/publish.yaml"

PLAN_JSON = "notify_plan.json"
PLAN_MARKDOWN = "notify_plan.md"


class MissingInputError(RuntimeError):
    pass


@dataclass
class NotifyConfig:
    """`configs/publish.yaml` の `notify` 節。

    **既定値は「動かない」側に置く。** sender_domain も URL も未設定なら
    計画は作れない。設定していないのに動いてしまうのが一番困る
    """

    sender_domain: str | None = None
    sender_dkim_selectors: list[str] = field(default_factory=list)
    detail_url: str | None = None
    method_url: str | None = None
    optout_contact: str | None = None
    optout_registry: str = "configs/notify/optout.csv"
    correction_days: int = CORRECTION_DAYS_RECOMMENDED
    rate_per_sec: float = 1.0
    #: security.txt を取りに HTTPS を叩くか。False なら「見ていない」と記録する
    fetch_security_txt: bool = True


def load_config(path: str | Path = DEFAULT_CONFIG) -> tuple[NotifyConfig, str | None]:
    """設定を読む。戻り値は (notify 節, tier2.correction_contact)。"""
    p = config_path(str(path))
    if not p.is_file():
        raise MissingInputError(f"設定が無い: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    section = raw.get("notify") or {}
    config = NotifyConfig(
        sender_domain=section.get("sender_domain"),
        sender_dkim_selectors=list(section.get("sender_dkim_selectors") or []),
        detail_url=section.get("detail_url"),
        method_url=section.get("method_url"),
        optout_contact=section.get("optout_contact"),
        optout_registry=section.get("optout_registry")
        or "configs/notify/optout.csv",
        correction_days=int(
            section.get("correction_days") or CORRECTION_DAYS_RECOMMENDED
        ),
        rate_per_sec=float(section.get("rate_per_sec") or 1.0),
        fetch_security_txt=bool(section.get("fetch_security_txt", True)),
    )
    correction_contact = (raw.get("tier2") or {}).get("correction_contact")
    return config, correction_contact


def _entity_names(run_id: str) -> dict[str, str]:
    frame = read_parquet(phase_output(run_id, "p1_population", "entities.parquet"))
    if frame is None or "entity_id" not in frame.columns:
        return {}
    name_col = next(
        (c for c in ("name", "name_ja", "name_official") if c in frame.columns), None
    )
    if name_col is None:
        return {}
    return {
        str(row["entity_id"]): str(row[name_col])
        for row in frame.to_dict(orient="records")
        if row.get(name_col)
    }


def _domain_names(run_id: str) -> dict[str, tuple[str, str | None]]:
    """domain_id → (ドメイン名, entity_id)。"""
    frame = read_parquet(phase_output(run_id, "p3_domains", "domains.parquet"))
    if frame is None:
        raise MissingInputError(
            f"{run_id} の P3 の出力が無い。先に p3-domains を実行すること"
        )
    out: dict[str, tuple[str, str | None]] = {}
    for row in frame.to_dict(orient="records"):
        domain_id = str(row.get("domain_id") or "")
        domain = str(row.get("domain") or "")
        if domain_id and domain:
            out[domain_id] = (domain, str(row.get("entity_id") or "") or None)
    return out


def _facts(run_id: str) -> list[dict[str, Any]]:
    frame = read_parquet(phase_output(run_id, "p5_parse", "facts.parquet"))
    if frame is None:
        raise MissingInputError(
            f"{run_id} の silver（facts）が無い。先に p5-parse を実行すること"
        )
    return [
        {k: (v.tolist() if hasattr(v, "tolist") else v) for k, v in row.items()}
        for row in frame.to_dict(orient="records")
    ]


def build_targets(
    run_id: str,
    *,
    fetch_https: bool = True,
    limit: int | None = None,
    only: list[str] | None = None,
) -> tuple[list[NotifyTarget], list[str]]:
    """通知候補を組み立てる。戻り値は (候補, 注記)。

    **指摘に当たる事実が無いドメインは候補にしない。** 連絡先を取りに
    HTTPS を叩く前に篩う。相手のサーバに無用のリクエストを出さない。
    """
    notes: list[str] = []
    domains = _domain_names(run_id)
    names = _entity_names(run_id)
    wanted = {d.lower() for d in (only or [])}

    targets: list[NotifyTarget] = []
    skipped_no_finding = 0
    skipped_unobserved = 0

    for fact in _facts(run_id):
        domain_id = str(fact.get("domain_id") or "")
        pair = domains.get(domain_id)
        if pair is None:
            continue
        domain, entity_id = pair
        if wanted and domain.lower() not in wanted:
            continue
        if not fact.get("observed"):
            skipped_unobserved += 1
            continue
        if not findings_from_fact(fact):
            skipped_no_finding += 1
            continue
        targets.append(
            NotifyTarget(
                domain=domain,
                fact=fact,
                contacts=ContactSet(domain=domain),
                entity_name=names.get(entity_id or ""),
                entity_id=entity_id,
            )
        )

    targets.sort(key=lambda t: t.domain)
    if limit is not None and limit > 0:
        if len(targets) > limit:
            notes.append(
                f"候補 {len(targets)} 件のうち先頭 {limit} 件だけを計画にした。"
                "**残りは計画に出ていない**（--limit の指定による）"
            )
        targets = targets[:limit]

    if skipped_unobserved:
        notes.append(
            f"{skipped_unobserved} 件は DNS を観測できていないため候補にしていない。"
            "「設定が無い」ではない（原則5）"
        )
    if skipped_no_finding:
        notes.append(
            f"{skipped_no_finding} 件は観測した範囲で指摘に当たる事実が無かった"
        )

    for target in targets:
        target.contacts = discover(target.domain, fetch_https=fetch_https)

    return targets, notes


def run(
    run_id: str,
    *,
    config: str | Path = DEFAULT_CONFIG,
    limit: int | None = None,
    only: list[str] | None = None,
    fetch_https: bool | None = None,
    notified_on: dt.date | None = None,
    resolver: Resolver | None = None,
    self_check: SelfComplianceResult | None = None,
    write: bool = True,
) -> tuple[NotifyPlan, dict[str, Any]]:
    """通知計画を作る。**送信はしない。**"""
    cfg, correction_contact = load_config(config)
    if not cfg.sender_domain:
        raise PlanBlockedError(
            "notify.sender_domain が未設定である。差出人のドメインが決まって"
            "いないと、本文の URL の真正性も自己準拠も検査できない"
        )
    if not cfg.detail_url or not cfg.method_url:
        raise PlanBlockedError(
            "notify.detail_url / notify.method_url が未設定である。"
            "**検証可能な正規ドメイン上の URL は文面の必須要件**である"
        )

    # 送信元自身の準拠。**自分のドメインも他社と同じ手続きで測る**
    if self_check is None:
        r = resolver or DnsResolver()
        self_check = check_self(
            cfg.sender_domain,
            resolver=r,
            selectors=cfg.sender_dkim_selectors or None,
        )

    registry = load_optout(config_path(cfg.optout_registry))

    targets, notes = build_targets(
        run_id,
        fetch_https=cfg.fetch_security_txt if fetch_https is None else fetch_https,
        limit=limit,
        only=only,
    )

    plan = build_plan(
        targets,
        sender_domain=cfg.sender_domain,
        self_check=self_check,
        registry=registry,
        detail_url=cfg.detail_url,
        method_url=cfg.method_url,
        correction_contact=correction_contact or "",
        measured_month=run_id,
        notified_on=notified_on,
        correction_days=cfg.correction_days,
        rate_per_sec=cfg.rate_per_sec,
        optout_contact=cfg.optout_contact,
    )
    plan.notes = notes + plan.notes

    written: dict[str, Any] = {"paths": []}
    if write:
        out_dir = phase_dir(run_id, PHASE)
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / PLAN_JSON
        md_path = out_dir / PLAN_MARKDOWN
        json_path.write_text(
            json.dumps(plan.to_dict(), ensure_ascii=False, indent=1, sort_keys=True),
            encoding="utf-8",
        )
        md_path.write_text(plan.to_markdown(), encoding="utf-8")
        written["paths"] = [str(json_path), str(md_path)]

    return plan, written
