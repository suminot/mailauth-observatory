"""リゾルバ間のクロスチェック（DESIGN.md P4「リゾルバ戦略」）。

`measure.yaml` の `resolver.cross_check` に従い、対象の一部を複数の
パブリックリゾルバで引いて差分を見る。全件を3系統で引くのは権威DNSへの
負荷が3倍になるので、既定は5%の抽出である。

**抽出は決定的にする。** 毎回違うドメインを引くと、月次で差分を追えない。
run_id を種にしてシャッフルする。
"""

from __future__ import annotations

import math
from typing import Any

from ..contracts import MeasureTier, QueryPurpose
from ..resolver import shuffled
from .backends.base import to_raw_response
from .bronze import BronzeWriter
from .plan import build_plan

#: クロスチェックで引くクエリ。全部引くと負荷が3倍になるので、
#: 手法差が出やすく件数の少ないものに絞る
CROSSCHECK_PURPOSES = (QueryPurpose.MX, QueryPurpose.SPF, QueryPurpose.DMARC)


def sample_targets(
    targets: list[dict], *, rate: float, run_id: str
) -> list[dict]:
    """決定的に抽出する。

    毎回違うドメインを引くと月次で差分を追えない。run_id を種にする。
    """
    if rate >= 1:
        return list(targets)
    if rate <= 0 or not targets:
        return []
    n = max(1, math.ceil(len(targets) * rate))
    seed = sum(ord(c) for c in run_id)
    order = shuffled([str(i) for i in range(len(targets))], seed)
    return [targets[int(i)] for i in order[:n]]


def measure_with(
    backend,
    targets: list[dict],
    *,
    run_id: str,
    method_label: str,
    bronze_root,
) -> dict[str, Any]:
    """1つのバックエンドで抽出対象を引き、bronze に書く。

    `method_label` が bronze のパーティション名になる。
    `dnspython@1.1.1.1` のように、どのリゾルバで引いたかが残る。
    """
    counts = {"queries": 0, "failed": 0}
    by_rcode: dict[str, int] = {}

    with BronzeWriter(bronze_root, method_label) as writer:
        for target in targets:
            domain = target["domain"]
            plan = [
                q
                for q in build_plan(domain, MeasureTier.C, selectors=[])
                if q.purpose in CROSSCHECK_PURPOSES
            ]
            for query in plan:
                answer = backend.query(query)
                writer.write(
                    to_raw_response(
                        query,
                        answer,
                        run_id=run_id,
                        domain=domain,
                        method=method_label,
                        tool_version=getattr(backend, "version", "unknown"),
                        resolver_label=getattr(backend, "resolver_label", method_label),
                    )
                )
                counts["queries"] += 1
                if not answer.observed:
                    counts["failed"] += 1
                by_rcode[answer.rcode] = by_rcode.get(answer.rcode, 0) + 1

    counts["by_rcode"] = dict(sorted(by_rcode.items()))
    counts["records"] = writer.records
    return counts
