"""DKIM セレクタの飽和曲線（DESIGN.md 7.2 画面5）。

**セレクタ数 対 新規発見ドメイン数**の曲線を出し、L1/L2 で足りているかを
判断できるようにする。L3（Tatang 辞書 3,498語）は GPL-3.0 のコピーレフト
波及の法務確認が済むまで無効にしてあるが、有効化する価値があるかどうかは
この曲線が示す。曲線が寝ていれば、辞書を増やしても新規発見はほとんど無い。

計算は bronze から行う。DNS は引かない。**同じ観測から何度でも引き直せる**
ようにしておくと、辞書を増やす前に「増やしたら何件増えるか」ではなく
「今の辞書のどこで止まっているか」を先に見られる。

読み方の注意
  順序に依存する。L1 の並び順を変えれば曲線の形も変わる。したがって
  **「何個目で飽和したか」ではなく「最後の何割が何件しか稼いでいないか」**
  を見る指標である。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ..contracts import QueryPurpose
from .bronze import iter_bronze_files, read_bronze


@dataclass
class SaturationPoint:
    """曲線の1点。"""

    #: 何個目のセレクタまで使ったか
    selectors_used: int
    selector: str
    #: このセレクタだけで見つかったドメイン（それまでのどれでも見つからなかった）
    new_domains: int
    #: ここまでの累積
    cumulative_domains: int
    #: このセレクタが当たったドメイン数（重複を含む）
    hits: int


@dataclass
class SaturationCurve:
    points: list[SaturationPoint] = field(default_factory=list)
    #: DKIM を検出できたドメインの総数
    detected_domains: int = 0
    #: セレクタを投げたドメインの総数（階層A のみ）
    probed_domains: int = 0
    #: 1件も新規を稼がなかったセレクタ
    dead_selectors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def selectors_tried(self) -> int:
        return len(self.points)

    def coverage_at(self, n: int) -> float | None:
        """先頭 n 個のセレクタで検出できたドメインの割合。"""
        if not self.detected_domains or not self.points:
            return None
        upto = [p for p in self.points if p.selectors_used <= n]
        if not upto:
            return 0.0
        return round(upto[-1].cumulative_domains / self.detected_domains, 4)

    def selectors_for(self, fraction: float) -> int | None:
        """検出できたドメインの `fraction` を占めるのに要したセレクタ数。

        「上位10個で9割取れている」を言えるようにする。
        """
        if not self.detected_domains:
            return None
        target = self.detected_domains * fraction
        for point in self.points:
            if point.cumulative_domains >= target:
                return point.selectors_used
        return None

    def to_dict(self) -> dict:
        return {
            "detected_domains": self.detected_domains,
            "probed_domains": self.probed_domains,
            "selectors_tried": self.selectors_tried,
            "dead_selectors": self.dead_selectors,
            "coverage_at_10": self.coverage_at(10),
            "coverage_at_20": self.coverage_at(20),
            "selectors_for_90pct": self.selectors_for(0.9),
            "selectors_for_99pct": self.selectors_for(0.99),
            "points": [
                {
                    "selectors_used": p.selectors_used,
                    "selector": p.selector,
                    "new_domains": p.new_domains,
                    "cumulative_domains": p.cumulative_domains,
                    "hits": p.hits,
                }
                for p in self.points
            ],
            "notes": self.notes,
        }


def selector_from(query_name: str) -> str | None:
    """`sel._domainkey.example.jp` からセレクタを取り出す。"""
    marker = "._domainkey."
    name = str(query_name or "")
    if marker not in name:
        return None
    return name.split(marker, 1)[0].lower() or None


def build(records: list[dict], *, order: list[str] | None = None) -> SaturationCurve:
    """bronze の行から曲線を作る。

    `order` はセレクタの評価順。省略すると当たった数の多い順にする。
    **順序に依存する指標であることを注記に残す。**

    **対照クエリ（DKIM_CONTROL）とワイルドカード探索は数えない。** 対照は
    実在しないセレクタなので、当たること自体が偽陽性の証拠であって
    「そのセレクタが有効だった」ことにはならない。
    """
    found: dict[str, set[str]] = defaultdict(set)
    probed: set[str] = set()

    for record in records:
        if str(record.get("purpose")) != QueryPurpose.DKIM:
            continue
        domain = str(record.get("domain") or "")
        if not domain:
            continue
        probed.add(domain)
        if not record.get("observed") or not record.get("record_present"):
            continue
        selector = selector_from(record.get("query_name", ""))
        if selector:
            found[selector].add(domain)

    curve = SaturationCurve(probed_domains=len(probed))
    if not found:
        curve.notes.append(
            "DKIM が検出できたドメインが無い。階層A の計測をしていないか、"
            "既知セレクタでは見つからなかった（未設定の証明ではない）"
        )
        return curve

    sequence = order or sorted(found, key=lambda s: (-len(found[s]), s))
    seen: set[str] = set()
    for index, selector in enumerate(sequence, start=1):
        domains = found.get(selector, set())
        new = domains - seen
        seen |= domains
        curve.points.append(
            SaturationPoint(
                selectors_used=index,
                selector=selector,
                new_domains=len(new),
                cumulative_domains=len(seen),
                hits=len(domains),
            )
        )
        if not new:
            curve.dead_selectors.append(selector)

    curve.detected_domains = len(seen)
    curve.notes.append(
        "順序に依存する指標である。辞書の並び順を変えれば曲線の形も変わる。"
        "「何個目で飽和したか」ではなく「最後の何割が何件しか稼いでいないか」を見る"
    )
    if curve.dead_selectors:
        curve.notes.append(
            f"1件も新規を稼がなかったセレクタが {len(curve.dead_selectors)} 個ある。"
            "辞書から落とす候補だが、**別の月には効くことがある**ので即座に消さない"
        )
    return curve


def from_bronze(bronze_root, *, order: list[str] | None = None) -> SaturationCurve:
    """bronze ディレクトリを読んで曲線を作る。"""
    records: list[dict] = []
    for path in iter_bronze_files(bronze_root):
        records.extend(read_bronze(path))
    return build(records, order=order)
