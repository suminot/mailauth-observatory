"""P6 推察。

fact からメール基盤とセキュリティ製品を推定する。**必ず confidence と
evidence を伴う**（原則2）。DNS は引かない。silver を読んで silver を書く。

辞書は `configs/fingerprints/*.yaml` にあり、コードには規則を1つも
書かない（原則7）。辞書を更新したら P6 だけを再実行すればよい。
"""

from .runner import (
    INFERENCE_VERSION,
    OUTPUT_FILENAME,
    PHASE,
    MissingInputError,
    run,
)

__all__ = [
    "run",
    "PHASE",
    "OUTPUT_FILENAME",
    "INFERENCE_VERSION",
    "MissingInputError",
]
