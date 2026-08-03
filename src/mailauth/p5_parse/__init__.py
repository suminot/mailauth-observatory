"""P5 パース。

bronze の生レスポンスを構造化し、仕様に照らして解釈する。
**このフェーズは何度でも作り直せる。** パーサにバグが見つかったら
bronze から再実行する。bronze には触らない。
"""

from .runner import OUTPUT_FILENAME, PARSER_VERSION, PHASE, MissingInputError, run

__all__ = ["run", "PHASE", "OUTPUT_FILENAME", "PARSER_VERSION", "MissingInputError"]
