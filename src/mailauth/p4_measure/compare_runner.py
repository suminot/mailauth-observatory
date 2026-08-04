"""手法比較の実行（コンソール画面4 の裏側）。

既定は**読み取り専用**である。bronze にあるパーティションを突き合わせる
だけで、DNS には出ない。`resolvers` を渡したときだけ、抽出したドメインを
各リゾルバで引いてから比べる。

比較結果は `p4_measure/compare.json` に置く。bronze には触らない。
"""

from __future__ import annotations

import json
from typing import Any

from ..config import load_measure_config
from ..contracts import MeasureTier
from ..io import read_parquet
from ..manifest import RunManifest
from ..paths import bronze_dir, phase_dir, phase_output
from . import compare as compare_mod
from . import crosscheck
from .runner import INPUT_FILENAME, INPUT_PHASE, MissingInputError, make_backend

PHASE = "p4_measure"
#: 比較の成果物は P4 の下のサブディレクトリに置く。
#: **P4 と同じディレクトリに置いてはいけない。** RunManifest は out_dir に
#: _manifest.json を書くので、計測の manifest を上書きしてしまう
COMPARE_DIR = "compare"
OUTPUT_FILENAME = "compare.json"
COMPARE_VERSION = "1.0.0"


def compare_dir(run_id: str):
    return phase_dir(run_id, PHASE) / COMPARE_DIR


def _targets(run_id: str, limit: int | None) -> list[dict]:
    frame = read_parquet(phase_output(run_id, INPUT_PHASE, INPUT_FILENAME))
    if frame is None:
        raise MissingInputError(
            f"{run_id} の domains.parquet がありません。先に p3-domains を実行してください"
        )
    measured = frame[frame["is_measured"].fillna(False).astype(bool)]
    targets = [
        {
            "domain": str(r["domain"]),
            "domain_id": str(r["domain_id"]),
            "tier": str(r["measure_tier"] or MeasureTier.C),
        }
        for _, r in measured.iterrows()
    ]
    return targets[:limit] if limit else targets


def run(
    run_id: str,
    *,
    methods: list[str] | None = None,
    resolvers: list[str] | None = None,
    sample_rate: float | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """手法・リゾルバの差分を出す。

    `methods` を省略すると bronze にあるパーティションをすべて比べる。
    `resolvers` を渡すと、その分だけ先に計測してから比べる（DNS に出る）。
    """
    out_dir = compare_dir(run_id)
    measure_cfg = load_measure_config()

    with RunManifest(
        run_id=run_id,
        phase=f"{PHASE}_compare",
        out_dir=out_dir,
        tool_versions={"compare": COMPARE_VERSION},
        params={
            "methods": methods,
            "resolvers": resolvers,
            "sample_rate": sample_rate,
            "limit": limit,
            "dry_run": dry_run,
        },
    ) as manifest:
        gathered: dict[str, Any] = {}

        if resolvers:
            cross_cfg = (measure_cfg.get("resolver") or {}).get("cross_check") or {}
            rate = (
                sample_rate
                if sample_rate is not None
                else float(cross_cfg.get("sample_rate", 0.05))
            )
            targets = crosscheck.sample_targets(
                _targets(run_id, limit), rate=rate, run_id=run_id
            )
            manifest.counts.input = len(targets)
            manifest.add_warning(
                "CROSSCHECK_QUERIES_DNS",
                count=len(targets),
                message=(
                    f"クロスチェックのため {len(targets)} ドメインを "
                    f"{len(resolvers)} 系統で引く（抽出率 {rate:.0%}）。"
                    "全件を複数系統で引くと権威DNSへの負荷が倍になる"
                ),
            )
            if dry_run:
                manifest.add_warning(
                    "DRY_RUN", message="dry_run のためクロスチェックを実行していない"
                )
            else:
                for nameserver in resolvers:
                    label = f"dnspython@{nameserver}"
                    backend = make_backend(label, measure_cfg)
                    gathered[label] = crosscheck.measure_with(
                        backend,
                        targets,
                        run_id=run_id,
                        method_label=label,
                        bronze_root=bronze_dir(run_id),
                    )

        available = compare_mod.available_methods(run_id)
        selected = methods or available
        missing = [m for m in selected if m not in available]
        if missing:
            manifest.add_warning(
                "METHOD_NOT_MEASURED",
                count=len(missing),
                sample=missing,
                message=(
                    f"bronze に無い手法を指定している: {missing}。"
                    "p4-measure --method <手法> で先に計測すること"
                ),
            )
            selected = [m for m in selected if m in available]

        result = compare_mod.compare(run_id, selected)
        manifest.counts.success = result.compared

        payload = {
            "run_id": run_id,
            "available_methods": available,
            "crosscheck": gathered,
            **result.to_dict(),
        }
        manifest.set_breakdown(
            available_methods=available,
            compared_methods=selected,
            compared=result.compared,
            counts=payload["counts"],
            agreement_rate=payload["agreement_rate"],
            crosscheck=gathered,
        )

        if len(selected) < 2:
            manifest.add_warning(
                "NOT_ENOUGH_METHODS",
                message=(
                    "比較には2つ以上の手法が必要。"
                    "p4-measure --method zdns などで別の手法でも計測すること"
                ),
            )
        if result.counts.get(compare_mod.PRESENCE_DIFFERS):
            manifest.add_warning(
                "PRESENCE_DIFFERS",
                count=result.counts[compare_mod.PRESENCE_DIFFERS],
                message=(
                    "両方が観測できたのにレコードの有無が食い違っている。"
                    "権威DNSの応答が揺れているか、どちらかに取りこぼしがある"
                ),
            )
        if result.counts.get(compare_mod.OBSERVATION_DIFFERS):
            manifest.add_warning(
                "OBSERVATION_DIFFERS",
                count=result.counts[compare_mod.OBSERVATION_DIFFERS],
                message=(
                    "片方が観測できていないクエリ。**手法の食い違いではない**。"
                    "その時たまたま引けなかっただけのことが多い"
                ),
            )

        if dry_run:
            manifest.add_warning("DRY_RUN", message="dry_run のため出力を書いていない")
        else:
            path = out_dir / OUTPUT_FILENAME
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            manifest.add_output(OUTPUT_FILENAME, records=result.compared)

    return manifest.to_dict()


def read_compare(run_id: str) -> dict[str, Any] | None:
    path = compare_dir(run_id) / OUTPUT_FILENAME
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
