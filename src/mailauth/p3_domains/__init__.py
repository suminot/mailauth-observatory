"""P3 メールドメイン確定。

候補ドメインのうち実際にメール送信に使われているものを絞り込み、
三段の確度フラグを付ける。本システムの中核。
"""

from .runner import OUTPUT_FILENAME, PHASE, MissingInputError, run

__all__ = ["run", "PHASE", "OUTPUT_FILENAME", "MissingInputError"]
