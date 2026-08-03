"""市場区分の付与。

JPX の `data_j.xls` は使わないという方針は変えない（DESIGN.md 1.4）。
そのため区分は「どこかから与えられた対応表」からしか付かない。

重要なのは、区分が付かないことを「区分が無い」と混同しないことである（原則5）。
対応表を与えていなければ `market_segment` は None のままで、
プライムのビューは「0社」ではなく「区分データが無いので判定できない」と報告する。

対応表の作り方は運用者に委ねる。各社の有価証券報告書の表紙（【上場金融商品取引所】）
には市場区分が記載されるため、EDINET から機械的に作るのが本筋だが、
それには EDINET API v2 の Subscription-Key が要る。当面は手作業の CSV でよい。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from .normalize import normalize_securities_code
from .paths import config_path

#: 正規化後の区分名。表記ゆれをここに寄せる。
SEGMENT_ALIASES = {
    "prime": "prime",
    "プライム": "prime",
    "プライム市場": "prime",
    "tse prime": "prime",
    "standard": "standard",
    "スタンダード": "standard",
    "スタンダード市場": "standard",
    "growth": "growth",
    "グロース": "growth",
    "グロース市場": "growth",
}

KNOWN_SEGMENTS = ("prime", "standard", "growth")


def normalize_segment(value: str | None) -> str | None:
    if not value:
        return None
    return SEGMENT_ALIASES.get(value.strip().lower(), value.strip().lower())


@dataclass
class SegmentMap:
    """証券コード（4桁）-> 市場区分。

    `source` は出典。成果物に出典を書けるようにするため必ず持たせる。
    """

    by_securities_code: dict[str, str] = field(default_factory=dict)
    source: str = "unknown"
    path: Path | None = None
    retrieved: str | None = None
    invalid_rows: int = 0

    def __len__(self) -> int:
        return len(self.by_securities_code)

    def get(self, securities_code: str | None) -> str | None:
        code = normalize_securities_code(securities_code)
        return self.by_securities_code.get(code) if code else None

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for seg in self.by_securities_code.values():
            out[seg] = out.get(seg, 0) + 1
        return dict(sorted(out.items()))


def load_segment_map(path: str | Path) -> SegmentMap:
    """区分の対応表を読む。

    想定する CSV（ヘッダ必須）:

        securities_code,market_segment,source,retrieved
        1234,プライム,有価証券報告書,2026-08-01

    `source` と `retrieved` は行ごとに書けるが、代表値を1つ採る。
    JPX の配布ファイルをそのまま置くことは方針として認めない。
    """
    p = config_path(str(path))
    if not p.is_file():
        raise FileNotFoundError(f"市場区分の対応表が見つかりません: {p}")

    mapping: dict[str, str] = {}
    sources: set[str] = set()
    retrieved: set[str] = set()
    invalid = 0

    with p.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "securities_code" not in reader.fieldnames:
            raise ValueError(
                f"{p} に securities_code 列がありません。"
                "ヘッダは securities_code,market_segment[,source,retrieved]"
            )
        for row in reader:
            code = normalize_securities_code(row.get("securities_code"))
            segment = normalize_segment(row.get("market_segment"))
            if not code or not segment:
                invalid += 1
                continue
            mapping[code] = segment
            if row.get("source"):
                sources.add(row["source"].strip())
            if row.get("retrieved"):
                retrieved.add(row["retrieved"].strip())

    return SegmentMap(
        by_securities_code=mapping,
        source=" / ".join(sorted(sources)) if sources else "unspecified",
        path=p,
        retrieved=max(retrieved) if retrieved else None,
        invalid_rows=invalid,
    )
