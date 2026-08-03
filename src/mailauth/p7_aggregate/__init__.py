"""P7 集計。

silver（facts + inferences）から gold を作る。**gold は Git にコミットする**
唯一の層なので、ここが公開物の内容を決める。

守っていること
  - 採用率の分母は観測できたドメインだけ（原則5）
  - n<5 の業種セルは秘匿し、秘匿セルが1つになるなら2次秘匿を行う
  - 「消えた」と「取れなかった」を混同しない
  - market_segment は内部専用なので gold には出さない
"""

from .runner import (
    AGGREGATOR_VERSION,
    BY_SECTOR_FILENAME,
    OVERALL_FILENAME,
    PHASE,
    MissingInputError,
    run,
)

__all__ = [
    "run",
    "PHASE",
    "OVERALL_FILENAME",
    "BY_SECTOR_FILENAME",
    "AGGREGATOR_VERSION",
    "MissingInputError",
]
