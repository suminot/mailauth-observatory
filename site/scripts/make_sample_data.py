"""サンプルページ用の数字を作る。

**これは観測値ではない。** 画面の見え方を確かめるためだけのもので、
`site/src/sample/` に置く。公開サイトには「サンプル」と明示した1ページ
としてだけ出る。

## なぜ手で JSON を書かないか

**本物の出力と形がずれても気付けないから。** 数字は作り物でよいが、
「どの列があるか」「日付がどう入るか」は本物と同じでなければ、
サンプルページで動いたものが本番で動かない（逆も起きる）。

そこで契約の型（`StatsOverall` / `StatsBySector`）から組み立て、
**P8 自身の書き出し関数**に渡す。列の増減も日付の形も、本番と同じ経路を通る。

## 使い方

    python site/scripts/make_sample_data.py

`site/src/sample/*.json` を書き直す。生成物はコミットする
（サイトのビルドに要るので、実行できない環境でも組めるようにしておく）。
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from mailauth.config import load_yaml  # noqa: E402
from mailauth.contracts import StatsBySector, StatsOverall  # noqa: E402
from mailauth.p8_publish.runner import _write_site_data, attribution_for  # noqa: E402

OUT = REPO / "site" / "src" / "sample"

MONTHS = ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"]

#: 共通12分類。configs/industry/edinet33_to_common12.csv と同じ並び
SECTORS = [
    ("1", "農林水産・鉱業", 41),
    ("2", "建設", 163),
    ("3", "素材・化学", 512),
    ("4", "機械・電機・精密", 604),
    ("5", "輸送用機器", 96),
    ("6", "その他製造・消費財", 287),
    ("7", "エネルギー・公益", 57),
    ("8", "商社・卸売・小売", 641),
    ("9", "運輸・物流", 131),
    ("10", "情報通信・IT・メディア", 588),
    ("11", "金融・保険・不動産", 449),
    ("12", "サービス・その他", 249),
]


def _rate(base: float, i: int, step: float) -> float:
    """月を追うごとに少しずつ動かす。**劇的に動かさない** ── 実際の月次変化は小さい。"""
    return base + step * i


def _overall(month: str, i: int) -> StatsOverall:
    entities = 3818 + i * 4
    domains = 2158 + i * 11
    observed = int(domains * 0.973)

    spf = int(observed * _rate(0.889, i, 0.0032))
    dmarc = int(observed * _rate(0.571, i, 0.0081))
    enforced = int(observed * _rate(0.243, i, 0.0067))
    nominal_reject = int(observed * _rate(0.148, i, 0.0044))
    # 名目 reject のうち pct< や t=y で実効しないぶんを引く
    effective_reject = int(nominal_reject * 0.79)

    sending = int(observed * 0.612)
    parked = observed - sending
    hardened = int(parked * _rate(0.318, i, 0.0092))
    defended = int(parked * 0.121)
    intentional = int(parked * 0.197)

    return StatsOverall(
        measured_month=dt.date.fromisoformat(f"{month}-01"),
        population_id="jp-all-listed",
        total_entities=entities,
        total_domains=domains,
        observed_domains=observed,
        spf_adopted_entities=int(entities * _rate(0.906, i, 0.0026)),
        spf_adopted_domains=spf,
        dmarc_adopted_entities=int(entities * _rate(0.604, i, 0.0074)),
        dmarc_adopted_domains=dmarc,
        dmarc_enforced_entities=int(entities * _rate(0.267, i, 0.0061)),
        dmarc_enforced_domains=enforced,
        nominal_reject_domains=nominal_reject,
        enforced_reject_domains=effective_reject,
        blind_reject_domains=int(nominal_reject * 0.27),
        dkim_detected_domains=int(observed * _rate(0.548, i, 0.0049)),
        dkim_not_found_domains=int(observed * _rate(0.331, i, -0.0038)),
        mta_sts_domains=int(observed * _rate(0.029, i, 0.0011)),
        tls_rpt_domains=int(observed * _rate(0.024, i, 0.0009)),
        bimi_domains=int(observed * _rate(0.013, i, 0.0006)),
        dnssec_domains=int(observed * _rate(0.016, i, 0.0002)),
        maturity_stage_dist=json.dumps(
            {
                "0": observed - spf,
                "1": spf - dmarc,
                "2": dmarc - enforced,
                "3": enforced - effective_reject,
                "4": effective_reject,
            },
            sort_keys=True,
        ),
        sending_domains=sending,
        sending_enforced=int(sending * _rate(0.351, i, 0.0083)),
        parked_domains=parked,
        parked_hardened=hardened,
        parked_defended=defended,
        parked_intentional=intentional,
        parked_neglected=parked - hardened - defended - intentional,
        park_defense_rate=round((hardened + defended) / parked, 4),
        dane_domains=int(observed * 0.002),
        dane_dnssec_valid=int(observed * 0.001),
        dane_orphan=int(observed * 0.001),
        delta_prev_month=json.dumps(
            {
                "domains_disappeared": 0 if i == 0 else 3,
                "domains_new": 0 if i == 0 else 11,
                "domains_unobserved_this_month": 0 if i == 0 else 18,
                "entities_new": 0 if i == 0 else 4,
                "entities_removed": 0 if i == 0 else 1,
                "notes": [] if i else ["前月の facts が無いため差分を計算していない"],
                "policy_downgraded": 0 if i == 0 else 2,
                "policy_upgraded": 0 if i == 0 else 14,
                "skipped": i == 0,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        spec_version="rfc7489+rfc9989",
    )


def _sectors(month: str, i: int) -> list[StatsBySector]:
    out: list[StatsBySector] = []
    for k, (code, label, n) in enumerate(SECTORS):
        # 業種ごとに少しずらす。**順位を付けるためではなく**、
        # 全部同じ数字だと画面の見え方が確かめられないため
        tilt = (k % 5 - 2) * 0.031
        domains = max(int(n * 0.57), 5)
        observed = int(domains * 0.973)
        dmarc = int(observed * min(max(_rate(0.571, i, 0.0081) + tilt, 0.2), 0.95))
        enforced = int(observed * min(max(_rate(0.243, i, 0.0067) + tilt * 0.7, 0.05), 0.7))
        out.append(
            StatsBySector(
                measured_month=dt.date.fromisoformat(f"{month}-01"),
                population_id="jp-all-listed",
                common12_code=code,
                common12_label=label,
                n_entities=n,
                total_entities=n,
                total_domains=domains,
                observed_domains=observed,
                spf_adopted_domains=int(
                    observed * min(_rate(0.889, i, 0.0032) + tilt * 0.4, 0.99)
                ),
                dmarc_adopted_domains=dmarc,
                dmarc_enforced_domains=enforced,
                nominal_reject_domains=int(enforced * 0.61),
                enforced_reject_domains=int(enforced * 0.48),
                dkim_detected_domains=int(
                    observed * min(_rate(0.548, i, 0.0049) + tilt, 0.95)
                ),
                dnssec_domains=int(observed * 0.016),
                suppressed=False,
                spec_version="rfc7489+rfc9989",
            )
        )
    # 秘匿の見え方も確かめたいので1件だけ束ねておく
    out.append(
        StatsBySector(
            measured_month=dt.date.fromisoformat(f"{month}-01"),
            population_id="jp-all-listed",
            common12_code="99",
            common12_label="その他（秘匿）",
            n_entities=7,
            total_entities=7,
            total_domains=6,
            observed_domains=6,
            suppressed=True,
            spec_version="rfc7489+rfc9989",
        )
    )
    return out


def main() -> int:
    overall = [_overall(m, i).model_dump(mode="json") for i, m in enumerate(MONTHS)]
    sectors = [
        s.model_dump(mode="json") for i, m in enumerate(MONTHS) for s in _sectors(m, i)
    ]

    cfg = load_yaml("configs/publish.yaml")
    # **JSON だけ出す。** CSV と Parquet は本物のデータの配布形式で、
    # サンプルに同じものを置くと取り違えのもとになる
    cfg = {**cfg, "tier1": {**(cfg.get("tier1") or {}), "formats": ["json"]}}

    written = _write_site_data(
        OUT,
        overall=overall,
        sectors=sectors,
        months=list(MONTHS),
        cfg=cfg,
        attribution=attribution_for({"jp-all-listed"}, cfg),
        excluded={"count": 0, "available": True, "note": "サンプルのため除外は無い"},
    )
    for path, n in written:
        print(f"  {path.relative_to(REPO)} ({n} records)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
