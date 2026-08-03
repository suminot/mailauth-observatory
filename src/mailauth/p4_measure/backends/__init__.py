"""計測バックエンド（DESIGN.md P4「バックエンドの抽象化」）。

複数手法を比較できることが要件なので、バックエンドはプラガブルにする。
どの手法で測ったかは成果物の意味を変えるため、暗黙の差し替えはしない。
"""

from .base import MeasureBackend, to_raw_response
from .dnspython_backend import DnspythonBackend

__all__ = ["MeasureBackend", "to_raw_response", "DnspythonBackend"]
