# site

公開サイト（Observable Framework）。P8 が生成したデータを読んで静的サイトを組む。

```bash
mailauth p8-publish --any-month   # gold からデータを書き出し、表現規約を検査する
npm install
npm run dev                       # http://localhost:3000
npm run build                     # dist/ に静的ファイルを出す
```

## P8 を通さずにビルドしないこと

`src/data/` は P8 の生成物である。gold を正本とし、同じ数字を二重に持たない。
`npm run build` を単独で走らせると古いデータのままサイトが組まれる。

より重要な理由として、P8 は次の3つを機械的に検査して**通らなければ停止する**。

1. 第1層に個社特定情報が混ざっていないか（列名で弾く）
2. ページに断定的な語彙や順位付けが無いか
3. 限界の明示がフッタに入っているか

これは法務要件であり、目視の確認では漏れる。CI も
`mailauth p8-publish --any-month` → `npm run build` の順で走らせている。

## 表現上の規約

`styles.css` の配色と `components/format.js` の文面生成に埋め込んである。

- 配色は3段階。赤の面積を最小化する
- 総合順位も A〜F グレードも付けない。達成した標準のチェックリストを出す
- `.observed` と `.inferred` で事実と推察を視覚的に分ける
- フッタに「本サイトは標準準拠の計測であり、総合的セキュリティ評価ではない」を常時表示

`tests/test_compliance.py` が `site/src/**/*.md` を検査している。

## 個社名付き明細（第2層）

まだ作っていない。公開するには事前通知から最低30日の訂正期間と、
Cloudflare Access 等による認証が必要（`configs/publish.yaml` を参照）。
条件を満たさない状態で有効化すると P8 が停止する。
