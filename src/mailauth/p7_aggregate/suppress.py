"""セル秘匿（DESIGN.md P7「セル秘匿」）。

公的統計の実務基準では、総務省統計局が n=1 または 2 を1次秘匿、米欧の多くの
機関は3または5未満を秘匿する（FCSM Statistical Policy Working Paper 22、
NIST SP 800-188）。k-匿名性の推奨 k は一般に3〜5で、実務では k=5 が広く
採用されている。本システムは保守的に **n=5** を採る。

**1次秘匿だけでは足りない。** 秘匿したセルがちょうど1つだと、全社合計から
他のセルを引けば秘匿した値が復元できる。これが2次秘匿の問題で、
DESIGN.md P7 の受け入れ基準「秘匿後の合計値から個社が逆算できないこと」が
指しているのはこれである。したがって秘匿対象が1つになった場合は、
**次に小さいセルも巻き込んで秘匿する**。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..contracts import MIN_CELL_SIZE

__all__ = ["MIN_CELL_SIZE", "SuppressionPlan", "plan", "is_recoverable"]


@dataclass
class SuppressionPlan:
    """どのセルを秘匿し、なぜそうしたか。"""

    #: 秘匿するセルのコード
    suppressed: list[str] = field(default_factory=list)
    #: 公開するセルのコード
    published: list[str] = field(default_factory=list)
    #: 2次秘匿のために追加で巻き込んだセル
    secondary: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def plan(
    counts: dict[str, int], *, threshold: int = MIN_CELL_SIZE
) -> SuppressionPlan:
    """業種コード -> 企業数 から秘匿計画を作る。

    `counts` は秘匿前の全セル。戻り値の `suppressed` を「その他」に束ねる。
    """
    result = SuppressionPlan()
    if not counts:
        return result

    # 1次秘匿。閾値未満のセル
    primary = sorted(code for code, n in counts.items() if n < threshold)
    remaining = sorted(code for code in counts if code not in primary)

    if len(primary) == 1 and remaining:
        # 秘匿セルが1つだと合計から引き算で復元できる。
        # 次に小さいセルを巻き込んで、少なくとも2つにする
        victim = min(remaining, key=lambda code: (counts[code], code))
        result.secondary = [victim]
        primary = sorted([*primary, victim])
        remaining = [code for code in remaining if code != victim]
        result.notes.append(
            f"秘匿セルが1つだけだと合計から逆算できるため、"
            f"次に小さいセル {victim}（n={counts[victim]}）も秘匿した（2次秘匿）"
        )

    result.suppressed = primary
    result.published = remaining
    if primary:
        result.notes.append(
            f"n<{threshold} のセルを {len(primary)} 件秘匿した"
            f"（{', '.join(primary)}）。細分軸での公開は避ける"
        )
    return result


def is_recoverable(
    counts: dict[str, int], suppressed: list[str], *, threshold: int = MIN_CELL_SIZE
) -> bool:
    """秘匿後の値が引き算で復元できてしまうか。

    受け入れ基準の検証用。復元できるのは次のどちらか。
      - 秘匿セルがちょうど1つ（合計 − 公開分 = そのセル）
      - 秘匿セルの合計が閾値未満（全部足しても閾値に届かないので、
        各セルの値の候補が極端に狭まる）
    """
    if not suppressed:
        return False
    if len(suppressed) == 1:
        return True
    return sum(counts.get(code, 0) for code in suppressed) < threshold
