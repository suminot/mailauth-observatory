"""手法・リゾルバの比較（DESIGN.md P4「バックエンドの抽象化」）。

**複数手法を比較できることが要件**である。bronze は `method=` で
パーティションが分かれているので、ここはそれを読んで差分を出す。

差分の分類が肝である。**「二つの手法が現実について食い違っている」ことと
「片方が失敗した」ことを混ぜない。** 後者は手法の優劣の話ですらなく、
その時たまたま引けなかっただけのことが多い（原則5）。

  presence_differs    両方観測できたが、レコードの有無が食い違う ── 最も重い
  values_differ       両方レコードありだが、値が違う
  observation_differs 片方が観測できていない ── 食い違いではない
  only_in             片方の計画にしか無いクエリ ── 計画が違うだけ
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..paths import bronze_dir
from .bronze import read_bronze

#: 各分類につき manifest に載せる例の数
SAMPLE_SIZE = 10

PRESENCE_DIFFERS = "presence_differs"
VALUES_DIFFER = "values_differ"
OBSERVATION_DIFFERS = "observation_differs"
ONLY_IN = "only_in"
AGREE = "agree"


@dataclass
class MethodStats:
    method: str
    records: int = 0
    observed: int = 0
    record_present: int = 0
    by_rcode: dict[str, int] = field(default_factory=dict)


@dataclass
class Divergence:
    kind: str
    domain: str
    query_name: str
    query_type: str
    purpose: str
    detail: dict[str, Any]


@dataclass
class CompareResult:
    methods: list[str] = field(default_factory=list)
    stats: dict[str, MethodStats] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    samples: dict[str, list[dict]] = field(default_factory=dict)
    #: 比較したクエリの総数（両方に存在したキー）
    compared: int = 0
    #: 片方の計画にしか無い purpose。**個別の差分として数えない。**
    #: クロスチェックは3 purpose しか引かないので、フル計測と突き合わせると
    #: 数百件の「片方にしかない」が出て、本当の食い違いが埋もれる
    plan_differs: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "methods": self.methods,
            "compared": self.compared,
            "plan_differs": self.plan_differs,
            "counts": dict(sorted(self.counts.items())),
            "agreement_rate": (
                round(self.counts.get(AGREE, 0) / self.compared, 4)
                if self.compared
                else None
            ),
            "stats": {
                m: {
                    "records": s.records,
                    "observed": s.observed,
                    "record_present": s.record_present,
                    "by_rcode": dict(sorted(s.by_rcode.items())),
                }
                for m, s in sorted(self.stats.items())
            },
            "samples": {k: self.samples[k] for k in sorted(self.samples)},
            "notes": self.notes,
        }


def available_methods(run_id: str) -> list[str]:
    """bronze にパーティションがある手法の一覧。"""
    root = bronze_dir(run_id)
    if not root.is_dir():
        return []
    return sorted(
        d.name.split("=", 1)[1]
        for d in root.iterdir()
        if d.is_dir() and d.name.startswith("method=")
    )


def load_method(run_id: str, method: str) -> list[dict]:
    root = Path(bronze_dir(run_id)) / f"method={method}"
    out: list[dict] = []
    for path in sorted(root.glob("part-*.jsonl.zst")):
        out.extend(read_bronze(path))
    return out


def _key(record: dict) -> tuple[str, str, str]:
    return (
        str(record.get("query_name", "")).rstrip(".").lower(),
        str(record.get("query_type", "")).upper(),
        str(record.get("purpose", "")),
    )


def _values(record: dict) -> list[str]:
    """比較用に値を正規化する。

    TXT は分割されたまま保存されているので連結する。順序は権威 DNS が
    返すたびに変わりうるのでソートする。**大文字小文字は落とさない。**
    SPF の include 先は大小を区別しないが、DKIM の公開鍵は区別する。
    """
    out: list[str] = []
    for answer in record.get("answers") or []:
        data = answer.get("data")
        if isinstance(data, list):
            out.append("".join(str(d) for d in data))
        elif data is not None:
            out.append(str(data).rstrip("."))
    return sorted(out)


def _stats(method: str, records: list[dict]) -> MethodStats:
    stats = MethodStats(method=method, records=len(records))
    for record in records:
        if record.get("observed"):
            stats.observed += 1
        if record.get("record_present"):
            stats.record_present += 1
        rcode = str(record.get("rcode") or "UNKNOWN")
        stats.by_rcode[rcode] = stats.by_rcode.get(rcode, 0) + 1
    return stats


def compare(run_id: str, methods: list[str]) -> CompareResult:
    """2つ以上の手法の bronze を突き合わせる。

    3つ以上渡された場合は先頭を基準にして総当たりで比べる。
    """
    result = CompareResult(methods=list(methods))
    if len(methods) < 2:
        result.notes.append(
            f"比較には2つ以上の手法が必要（今あるのは {methods}）。"
            "別の --method で p4-measure を実行すると比べられる"
        )
        return result

    loaded: dict[str, dict[tuple, dict]] = {}
    for method in methods:
        records = load_method(run_id, method)
        result.stats[method] = _stats(method, records)
        indexed: dict[tuple, dict] = {}
        for record in records:
            # 同じキーが複数回あるのは再実行で追記された場合。最後を採る
            indexed[_key(record)] = record
        loaded[method] = indexed

    base_method = methods[0]
    base = loaded[base_method]
    divergences: list[Divergence] = []

    for other_method in methods[1:]:
        other = loaded[other_method]

        # 計画自体が違う purpose は個別の差分として数えない。
        # クロスチェックは MX / SPF / DMARC しか引かないので、フル計測と
        # 突き合わせると数百件の「片方にしかない」が出て、本当の食い違いが埋もれる
        purposes_a = {k[2] for k in base}
        purposes_b = {k[2] for k in other}
        shared = purposes_a & purposes_b
        only_purposes = {
            m: sorted(p)
            for m, p in (
                (base_method, purposes_a - purposes_b),
                (other_method, purposes_b - purposes_a),
            )
            if p
        }
        if only_purposes:
            result.plan_differs[f"{base_method} vs {other_method}"] = only_purposes

        for key in sorted(set(base) | set(other)):
            if key[2] not in shared:
                continue
            a, b = base.get(key), other.get(key)
            query_name, query_type, purpose = key

            if a is None or b is None:
                present_in = base_method if b is None else other_method
                divergences.append(
                    Divergence(
                        kind=ONLY_IN,
                        domain=str((a or b).get("domain", "")),
                        query_name=query_name,
                        query_type=query_type,
                        purpose=purpose,
                        detail={"present_in": present_in},
                    )
                )
                continue

            result.compared += 1
            domain = str(a.get("domain", ""))

            if not a.get("observed") or not b.get("observed"):
                # 片方が引けていない。**手法の食い違いではない**
                divergences.append(
                    Divergence(
                        kind=OBSERVATION_DIFFERS,
                        domain=domain,
                        query_name=query_name,
                        query_type=query_type,
                        purpose=purpose,
                        detail={
                            base_method: {
                                "observed": bool(a.get("observed")),
                                "rcode": a.get("rcode"),
                            },
                            other_method: {
                                "observed": bool(b.get("observed")),
                                "rcode": b.get("rcode"),
                            },
                        },
                    )
                )
                continue

            if bool(a.get("record_present")) != bool(b.get("record_present")):
                # 両方引けたのに有無が食い違う。最も重い差分
                divergences.append(
                    Divergence(
                        kind=PRESENCE_DIFFERS,
                        domain=domain,
                        query_name=query_name,
                        query_type=query_type,
                        purpose=purpose,
                        detail={
                            base_method: bool(a.get("record_present")),
                            other_method: bool(b.get("record_present")),
                        },
                    )
                )
                continue

            va, vb = _values(a), _values(b)
            if va != vb:
                divergences.append(
                    Divergence(
                        kind=VALUES_DIFFER,
                        domain=domain,
                        query_name=query_name,
                        query_type=query_type,
                        purpose=purpose,
                        detail={base_method: va, other_method: vb},
                    )
                )
                continue

            result.counts[AGREE] = result.counts.get(AGREE, 0) + 1

    by_kind: dict[str, list[Divergence]] = defaultdict(list)
    for d in divergences:
        by_kind[d.kind].append(d)
    for kind, items in by_kind.items():
        result.counts[kind] = len(items)
        result.samples[kind] = [
            {
                "domain": d.domain,
                "query_name": d.query_name,
                "query_type": d.query_type,
                "purpose": d.purpose,
                "detail": d.detail,
            }
            for d in items[:SAMPLE_SIZE]
        ]

    if result.counts.get(PRESENCE_DIFFERS):
        result.notes.append(
            "両方が観測できたのにレコードの有無が食い違っているクエリがある。"
            "権威DNSの応答が揺れているか、どちらかの手法に取りこぼしがある"
        )
    if result.counts.get(ONLY_IN):
        result.notes.append(
            "同じ purpose の中で片方にしか無いクエリがある。DKIM のセレクタ推定は"
            "MX / SPF の結果に依存するので、対象ドメインごとに計画がずれることがある"
        )
    if result.plan_differs:
        result.notes.append(
            "そもそも引いている purpose が違う組み合わせがある。"
            "クロスチェックは MX / SPF / DMARC しか引かないため、"
            "フル計測との比較では共通の purpose だけを突き合わせている"
        )
    return result
