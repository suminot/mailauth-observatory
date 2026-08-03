"""P4 DNS計測。

確定したドメインについてメール認証に関わる全レコードを取得し、
生のまま bronze に保存する（原則1 ── 生データは不変）。
"""

from .runner import PHASE, MissingInputError, UnknownBackendError, run

__all__ = ["run", "PHASE", "MissingInputError", "UnknownBackendError"]
