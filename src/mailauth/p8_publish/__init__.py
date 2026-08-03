"""P8 公開。

gold を公開サイトのデータに書き出し、表現上の規約を検査する。
デプロイ自体は CI か手元の wrangler が行う。このフェーズの責務は
「**出してよいものだけを出す**」ことである。

第2層（個社名付き明細）は既定で出さない。訂正期間を経ていない個社明細を
公開すると取り返しがつかないため、日数の判定はコードで行う。
"""

from .runner import (
    PHASE,
    PUBLISHER_VERSION,
    MissingInputError,
    PublishBlockedError,
    run,
)

__all__ = [
    "run",
    "PHASE",
    "PUBLISHER_VERSION",
    "MissingInputError",
    "PublishBlockedError",
]
