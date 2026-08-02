"""計測バックエンド（DESIGN.md P4「バックエンドの抽象化」）── 未実装。

複数手法を比較できることが要件なので、バックエンドはプラガブルにする。

    class MeasureBackend(Protocol):
        name: str
        def query(self, name: str, rtype: str, resolver: str) -> RawResponse: ...

予定: zdns（主力）/ dnsx（クロスチェック）/ dnspython（検証）/ securitytrails（過去データのみ）
"""
