"""P6 推察の本体。

fact からメール基盤とセキュリティ製品を推定する。**必ず confidence と
evidence を伴う**（原則2）。silver を読んで silver を書くので、辞書を
更新したら何度でも作り直せる。DNS は一切引かない。

未知 MX ホストの頻度順リストがこのフェーズの最も重要な副産物である。
国内ベンダーの固定ホスト名は公開情報から特定できないものが多く、
実測データからの帰納的発見でしか辞書が埋まらない（DESIGN.md P6 実装メモ）。
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from typing import Any

from ..contracts import (
    INFERENCE_ARROW_SCHEMA,
    INFERENCE_SORT_KEYS,
    Inference,
    InferenceCategory,
)
from ..io import read_parquet, write_parquet
from ..manifest import RunManifest
from ..p5_parse.orgdomain import resolve_psl
from ..paths import month_date, phase_dir, phase_output, previous_run_id
from . import park as park_mod
from .fingerprints import RuleSet, load_all
from .match import (
    NOT_DETECTED_VENDOR,
    build_drafts,
    undetectable_draft,
    vendor_categories,
)

PHASE = "p6_infer"
OUTPUT_FILENAME = "inferences.parquet"
INFERENCE_VERSION = "1.0.0"

#: manifest に載せる未知 MX ホストの件数。ここから辞書を育てる
UNKNOWN_MX_TOP_N = 20
#: 受け入れ基準（DESIGN.md P6）。推定が1件も付かないドメインの許容割合
MAX_NO_INFERENCE_RATE = 0.20
#: stale の連続月数を引き継ぐために遡る月数
STALE_HISTORY_MONTHS = 3


class MissingInputError(RuntimeError):
    pass


def inference_id(domain_id: str, run_id: str, category: str, vendor: str) -> str:
    digest = hashlib.sha256(
        f"{domain_id}|{run_id}|{category}|{vendor}".encode()
    ).hexdigest()
    return f"i:{digest[:16]}"


def _clean(value: Any) -> Any:
    """parquet 由来の NaN / numpy を Pydantic が通る形に落とす。"""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if hasattr(value, "tolist"):
        return value.tolist()
    return value


def facts_from_parquet(frame) -> list[dict]:
    """facts.parquet を素の dict の列に戻す。

    照合は dict のキー参照で足りるので Fact を再構築しない。
    列が増えても P6 を触らずに済む。
    """
    out: list[dict] = []
    for _, row in frame.iterrows():
        out.append({key: _clean(row[key]) for key in frame.columns})
    return out


def load_prior_streaks(run_id: str) -> dict[str, dict[tuple[str, str], int]]:
    """前月までの stale 連続月数を読む。

    {domain_id: {(category, vendor): 連続月数}}。前月の出力が無ければ空。
    **無いことをエラーにしない。** 初回実行では必ず無いし、
    「履歴が無い」は「裏付けがある」でも「無い」でもない（原則5）。
    """
    prior = previous_run_id(run_id)
    frame = read_parquet(phase_output(prior, PHASE, OUTPUT_FILENAME))
    if frame is None:
        return {}

    out: dict[str, dict[tuple[str, str], int]] = defaultdict(dict)
    for _, row in frame.iterrows():
        streak = _clean(row.get("stale_streak_months"))
        if streak in (None, 0):
            continue
        key = (str(row["category"]), str(row["vendor"]))
        out[str(row["domain_id"])][key] = int(streak)
    return dict(out)


def unknown_mx_hosts(
    facts: list[dict], matched_hosts: set[str]
) -> list[dict[str, Any]]:
    """辞書に一致しなかった MX ホストを頻度順にまとめる。

    集約は**ホスト名そのものではなく登録ドメイン単位**で行う。
    `mx1.cust0042.example-vendor.co.jp` のような顧客別ホスト名は
    1件ずつ数えても辞書を育てる手がかりにならない。
    """
    counts: dict[str, int] = defaultdict(int)
    examples: dict[str, list[str]] = defaultdict(list)
    for fact in facts:
        for host in fact.get("mx_hosts") or []:
            normalized = (host or "").strip().rstrip(".").lower()
            if not normalized or normalized in matched_hosts:
                continue
            key = resolve_psl(normalized) or normalized
            counts[key] += 1
            if len(examples[key]) < 3 and normalized not in examples[key]:
                examples[key].append(normalized)

    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [
        {"registered_domain": key, "count": count, "examples": examples[key]}
        for key, count in ranked[:UNKNOWN_MX_TOP_N]
    ]


def run(
    run_id: str,
    *,
    limit: int | None = None,
    dry_run: bool = False,
    rule_set: RuleSet | None = None,
) -> dict[str, Any]:
    """P6 を実行し manifest の内容を返す。"""
    out_dir = phase_dir(run_id, PHASE)
    facts_path = phase_output(run_id, "p5_parse", "facts.parquet")

    with RunManifest(
        run_id=run_id,
        phase=PHASE,
        out_dir=out_dir,
        tool_versions={"inference": INFERENCE_VERSION},
        params={"limit": limit, "dry_run": dry_run},
    ) as manifest:
        frame = read_parquet(facts_path)
        if frame is None:
            raise MissingInputError(
                f"{facts_path} がありません。先に p5-parse を実行してください"
            )

        rules = rule_set if rule_set is not None else load_all()
        manifest.tool_versions["fingerprints"] = rules.version
        categories = vendor_categories(rules)

        facts = facts_from_parquet(frame)
        if limit:
            facts = facts[:limit]
        manifest.counts.input = len(facts)

        prior_streaks = load_prior_streaks(run_id)
        if not prior_streaks:
            manifest.add_warning(
                "NO_STALE_HISTORY",
                message=(
                    "前月の inferences.parquet が無いため stale の連続月数を"
                    f"引き継げない。{STALE_HISTORY_MONTHS} か月連続の判定は"
                    "今月を1か月目として数え直す"
                ),
            )

        month = month_date(run_id)
        inferences: list[Inference] = []
        counters: dict[str, int] = defaultdict(int)
        by_category: dict[str, int] = defaultdict(int)
        by_confidence: dict[str, int] = defaultdict(int)
        by_park: dict[str, int] = defaultdict(int)
        matched_hosts: set[str] = set()
        no_inference_domains: list[str] = []
        notes_sample: list[str] = []

        for fact in facts:
            domain_id = str(fact.get("domain_id") or "")
            entity_id = str(fact.get("entity_id") or "unknown")

            try:
                drafts = build_drafts(
                    fact,
                    rules,
                    categories=categories,
                    prior_streaks=prior_streaks.get(domain_id, {}),
                )
                parked = park_mod.classify(fact)
            except Exception as exc:  # noqa: BLE001 - 1件で全体を止めない
                manifest.add_failure(f"infer_error:{type(exc).__name__}")
                notes_sample.append(f"{domain_id}: {type(exc).__name__}: {exc}")
                continue

            for draft in drafts:
                for hit in draft.hits:
                    if hit.record_type == "MX":
                        matched_hosts.add(hit.matched_value.strip().rstrip(".").lower())

            if not drafts:
                no_inference_domains.append(domain_id)

            # security_gateway が1件も立たなければ盲点を1行として残す。
            # 「検出0件」と「痕跡を残さない製品を使っている」は区別できない
            has_gateway = any(
                d.category == InferenceCategory.SECURITY_GATEWAY for d in drafts
            )
            if not has_gateway:
                drafts = [*drafts, undetectable_draft(rules)]
                counters["undetectable_security_gateway"] += 1

            by_park[str(parked.park_class)] += 1
            if parked.park_class is None:
                counters["park_unclassified"] += 1

            for index, draft in enumerate(drafts):
                by_category[draft.category] += 1
                by_confidence[draft.confidence] += 1
                if draft.is_stale:
                    counters["stale_verification"] += 1
                elif draft.stale_streak_months:
                    counters["stale_pending"] += 1

                notes = [n for n in draft.notes if n]
                inferences.append(
                    Inference(
                        inference_id=inference_id(
                            domain_id, run_id, draft.category, draft.vendor
                        ),
                        domain_id=domain_id,
                        entity_id=entity_id,
                        run_id=run_id,
                        measured_month=month,
                        category=draft.category,
                        vendor=draft.vendor,
                        product=draft.product,
                        confidence=draft.confidence,
                        is_stale=draft.is_stale,
                        stale_streak_months=draft.stale_streak_months,
                        evidence=draft.evidence_json(),
                        rule_ids=draft.rule_ids,
                        fingerprint_version=rules.version,
                        note=" / ".join(notes) or None,
                        undetectable_reason=draft.undetectable_reason,
                        # パーク分類はドメイン単位の属性なので先頭行にだけ載せる。
                        # 全行に複製すると集計でドメインを二重に数える
                        park_class=parked.park_class if index == 0 else None,
                        park_has_null_mx=parked.has_null_mx if index == 0 else None,
                        park_has_wildcard_dkim_revoked=(
                            parked.has_wildcard_dkim_revoked if index == 0 else None
                        ),
                    )
                )
                if index == 0:
                    notes_sample.extend(parked.notes[:1])

        manifest.counts.success = len(inferences)

        unknown = unknown_mx_hosts(facts, matched_hosts)
        no_inference_rate = (
            len(no_inference_domains) / len(facts) if facts else 0.0
        )

        manifest.set_breakdown(
            **dict(counters),
            by_category=dict(sorted(by_category.items())),
            by_confidence=dict(sorted(by_confidence.items())),
            by_park_class=dict(sorted(by_park.items())),
            domains_with_no_inference=len(no_inference_domains),
            no_inference_rate=round(no_inference_rate, 4),
            # **辞書を育てる主要な経路。** ここを見て手で辞書に追記する
            unknown_mx_hosts=unknown,
            rules_loaded=len(rules.rules),
            undetectable_products=len(rules.undetectable),
            notes_sample=notes_sample[:20],
        )

        if no_inference_rate > MAX_NO_INFERENCE_RATE:
            manifest.add_warning(
                "NO_INFERENCE_RATE_HIGH",
                count=len(no_inference_domains),
                message=(
                    f"推定が付かないドメインが {no_inference_rate:.1%} で受け入れ基準"
                    f"（{MAX_NO_INFERENCE_RATE:.0%} 未満）を超えている。"
                    "unknown_mx_hosts から辞書を拡充する"
                ),
            )
        if unknown:
            manifest.add_warning(
                "UNKNOWN_MX_HOSTS",
                count=len(unknown),
                message=(
                    "辞書に無い MX ホストがある。頻度順の上位を手で辞書に追記すると"
                    "推定率が上がる（DESIGN.md P6 実装メモ）"
                ),
            )
        if counters.get("undetectable_security_gateway"):
            manifest.add_warning(
                "SECURITY_GATEWAY_UNDETECTABLE",
                count=counters["undetectable_security_gateway"],
                message=(
                    "セキュリティ製品を DNS 上で検出できなかったドメイン。"
                    "API / OAuth 連携型の製品は原理的に痕跡を残さないため、"
                    "「使っていない」と解釈してはならない"
                ),
            )
        if counters.get("park_unclassified"):
            manifest.add_warning(
                "PARK_UNCLASSIFIED",
                count=counters["park_unclassified"],
                message=(
                    "観測できなかったためパーク分類をしていないドメイン。"
                    "neglected（放置）とは異なる"
                ),
            )

        if dry_run:
            manifest.add_warning("DRY_RUN", message="dry_run のため出力を書いていない")
        else:
            n = write_parquet(
                inferences,
                out_dir / OUTPUT_FILENAME,
                INFERENCE_ARROW_SCHEMA,
                sort_keys=INFERENCE_SORT_KEYS,
                metadata={
                    "mailauth.phase": PHASE,
                    "mailauth.run_id": run_id,
                    "mailauth.fingerprint_version": rules.version,
                },
            )
            manifest.add_output(OUTPUT_FILENAME, records=n)

    return manifest.to_dict()


def vendor_share(inferences_frame, category: str) -> list[dict[str, Any]]:
    """ベンダー別の件数。コンソールと P7 の下ごしらえ。

    `not_detected`（API 連携型製品の盲点を示す番兵）は**除外する**。
    シェアの分母に混ぜると「未検出」が1ベンダーとして数えられてしまう。
    """
    counts: dict[str, int] = defaultdict(int)
    for _, row in inferences_frame.iterrows():
        if str(row["category"]) != category:
            continue
        vendor = str(row["vendor"])
        if vendor == NOT_DETECTED_VENDOR:
            continue
        counts[vendor] += 1
    return [
        {"vendor": vendor, "count": count}
        for vendor, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
