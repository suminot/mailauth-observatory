"""P1 母集団確定。

対象企業のリストを取得し、一意識別子と業種分類を付与して保全する。
"""

from .runner import OUTPUT_FILENAME, PHASE, PopulationNotImplementedError, run

__all__ = ["run", "PHASE", "OUTPUT_FILENAME", "PopulationNotImplementedError"]
