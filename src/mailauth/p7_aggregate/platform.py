"""メール基盤・前段の集計（DESIGN-platform.md §1、§6）。

P6 の推定（`inferences.parquet`）を、母集団 × 層 × ベンダーの表にする。

## 数えない行が3種類ある

**推定が1件あることと、使っていることは別である。**

  `undetectable_reason` が入っている行
      「検出できなかった」ことの記録であって、利用の証拠ではない。
      Microsoft の仮 MX のように、**ベンダーは分かるのに使っているとは
      言えない**痕跡がある

  `is_layer_primary` が False の行
      同じ層に2つ立ったときの、代表でない方。両方数えると合計が
      100%を超える

  番兵（`not_detected`）
      API 連携型の盲点を示すための行

## 分母を2つ出す

観測できたドメイン全部と、その層で基盤を同定できたドメインだけ。
**片方だけだと誤読される**（運営者の判断）。

## ベンダー名も秘匿の対象にする

業種別と同じ理屈である。「この製品を使っているのは1社」は個社の特定に
つながりうる。n<`MIN_CELL_SIZE` は「その他」に束ねる。**件数は残す** ──
束ねた先の合計として出るので、分母が閉じたままになる（原則4）。
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from ..contracts import (
    MIN_CELL_SIZE,
    SUPPRESSED_VENDOR,
    EvidenceRecordType,
    StatsPlatform,
)
from ..p6_infer.match import NOT_DETECTED_VENDOR

__all__ = ["aggregate_platform", "counts_as_using"]


def counts_as_using(row: dict) -> bool:
    """この推定を「使っている」として数えてよいか。

    **ここが唯一の入口である。** 数える場所ごとに条件を書くと、片方を
    直し忘れて数字が食い違う。
    """
    if str(row.get("vendor") or "") == NOT_DETECTED_VENDOR:
        return False
    reason = row.get("undetectable_reason")
    if isinstance(reason, str) and reason:
        return False
    if row.get("is_layer_primary") is False:
        return False
    return bool(row.get("layer"))


def _is_receiving(row: dict) -> bool:
    """MX がそこを指しているか。**最も堅い証拠**（DESIGN-platform.md §4）。

    evidence は JSON 文字列。**読めなければ「受信している」とは言わない**
    （原則5：分からないものを断定しない）。
    """
    raw = row.get("evidence")
    if not isinstance(raw, str) or not raw:
        return False
    try:
        items = json.loads(raw)
    except (TypeError, ValueError):
        return False
    if not isinstance(items, list):
        return False
    return any(
        isinstance(i, dict) and i.get("record_type") == EvidenceRecordType.MX
        for i in items
    )


def aggregate_platform(
    inferences: list[dict],
    *,
    measured_month,
    population_id: str,
    members: set[str],
    observed_domains: int,
    observed_entities: int,
    threshold: int = MIN_CELL_SIZE,
) -> list[StatsPlatform]:
    """1母集団分の表を作る。

    `members` はその母集団の企業 id。**母集団の外の推定は数えない。**
    `observed_*` は分母（観測できた量）で、呼び出し側が持っている。
    """
    # (layer, vendor) -> 集計
    any_domains: dict[tuple[str, str], set[str]] = defaultdict(set)
    any_entities: dict[tuple[str, str], set[str]] = defaultdict(set)
    rx_domains: dict[tuple[str, str], set[str]] = defaultdict(set)
    rx_entities: dict[tuple[str, str], set[str]] = defaultdict(set)
    engines: dict[tuple[str, str], set[str]] = defaultdict(set)
    # 層ごとの「同定できた」量。分母の片方になる
    identified_domains: dict[str, set[str]] = defaultdict(set)
    identified_entities: dict[str, set[str]] = defaultdict(set)

    for row in inferences:
        entity_id = str(row.get("entity_id") or "")
        if entity_id not in members:
            continue
        if not counts_as_using(row):
            continue
        layer = str(row.get("layer"))
        vendor = str(row.get("vendor") or "")
        domain_id = str(row.get("domain_id") or "")
        key = (layer, vendor)

        any_domains[key].add(domain_id)
        any_entities[key].add(entity_id)
        identified_domains[layer].add(domain_id)
        identified_entities[layer].add(entity_id)
        engine = row.get("engine")
        if isinstance(engine, str) and engine:
            engines[key].add(engine)
        if _is_receiving(row):
            rx_domains[key].add(domain_id)
            rx_entities[key].add(entity_id)

    # -- 秘匿。**企業数で判定する**（個社の特定につながるのはそちら）-------
    out: list[StatsPlatform] = []
    bundled: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"ad": set(), "ae": set(), "rd": set(), "re": set()}
    )

    for (layer, vendor), domains in sorted(any_domains.items()):
        entities = any_entities[(layer, vendor)]
        if len(entities) < threshold:
            b = bundled[layer]
            b["ad"] |= domains
            b["ae"] |= entities
            b["rd"] |= rx_domains[(layer, vendor)]
            b["re"] |= rx_entities[(layer, vendor)]
            continue
        engine_set = engines[(layer, vendor)]
        out.append(
            StatsPlatform(
                measured_month=measured_month,
                population_id=population_id,
                layer=layer,
                vendor=vendor,
                # **1つに決まらなければ出さない。** 勝手に選ぶと嘘になる
                engine=next(iter(engine_set)) if len(engine_set) == 1 else None,
                entities_any=len(entities),
                domains_any=len(domains),
                entities_receiving=len(rx_entities[(layer, vendor)]),
                domains_receiving=len(rx_domains[(layer, vendor)]),
                observed_domains=observed_domains,
                identified_domains=len(identified_domains[layer]),
                observed_entities=observed_entities,
                identified_entities=len(identified_entities[layer]),
            )
        )

    for layer, b in sorted(bundled.items()):
        out.append(
            StatsPlatform(
                measured_month=measured_month,
                population_id=population_id,
                layer=layer,
                vendor=SUPPRESSED_VENDOR,
                entities_any=len(b["ae"]),
                domains_any=len(b["ad"]),
                entities_receiving=len(b["re"]),
                domains_receiving=len(b["rd"]),
                observed_domains=observed_domains,
                identified_domains=len(identified_domains[layer]),
                observed_entities=observed_entities,
                identified_entities=len(identified_entities[layer]),
                suppressed=True,
            )
        )

    return sorted(out, key=lambda s: (s.layer, s.vendor))


def summary(rows: list[StatsPlatform]) -> dict[str, Any]:
    """manifest に載せる要約。**秘匿した量も出す**（原則4）。"""
    by_layer: dict[str, int] = defaultdict(int)
    suppressed: dict[str, int] = defaultdict(int)
    for row in rows:
        by_layer[row.layer] += 1
        if row.suppressed:
            suppressed[row.layer] += row.entities_any
    return {
        "vendors_by_layer": dict(sorted(by_layer.items())),
        "suppressed_entities_by_layer": dict(sorted(suppressed.items())),
    }
