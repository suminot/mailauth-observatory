"""P2 ドメイン候補生成。

企業1社に対し、その企業が保有している可能性のあるドメイン群を展開する。
ここではまだ絞らない。再現率を優先する。
"""

from .runner import OUTPUT_FILENAME, PHASE, MissingInputError, run

__all__ = ["run", "PHASE", "OUTPUT_FILENAME", "MissingInputError"]
