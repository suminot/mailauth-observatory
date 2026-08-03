# 手動メンテナンスの関連ドメイン辞書

グループ会社や事業ブランドのドメインを人手で追加する経路。
DESIGN.md P2 が「人手で追加できる経路を必ず用意する」と定めている。

CT ログ・SPF redirect・DMARC rua の三経路は自動だが、次のような
ドメインは自動では拾えない。ここに書く。

- 証明書を取っていない送信専用ドメイン
- 親会社と証明書もDNSも共有していないグループ会社
- 買収前のブランドで残っているドメイン

## 形式

```csv
entity_id,domain,note,added
jp:1234567890123,example-group.co.jp,グループ会社。IRサイトに記載,2026-08-03
jp:1234567890123,example-brand.jp,事業ブランド,2026-08-03
```

- `entity_id` は `entities.parquet` の値（`jp:法人番号` / `edinet:コード` / 将来 `lei:`）
- `domain` は eTLD+1。サブドメインを書いても apex に丸められる
- `note` に根拠を書く。あとから検証できるように
- `added` は追加日

`manual` は「独立した裏付け」として扱われる（`configs/candidates.yaml` の
`confidence.independent_methods`）。根拠のない推測を書くと確度判定を
汚すので、必ず出典を `note` に残すこと。
