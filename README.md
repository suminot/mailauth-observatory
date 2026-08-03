# mailauth-observatory

日本と米国の上場企業を対象に、各社が実際にメール送信に用いているドメインを同定し、
そのメール認証設定（SPF / DKIM / DMARC / BIMI / MTA-STS / TLS-RPT / DNSSEC）と
利用メール基盤を月次で計測し、時系列として蓄積・公開するシステム。

設計仕様は [DESIGN.md](DESIGN.md)、その前提となる調査は
[research-dossier.html.html](research-dossier.html.html) にある。**実装前に DESIGN.md を読むこと。**

## このシステムが提供する固有の価値

1. **企業とドメインの対応を、確度を明示して透明に公開する。** 既存の商用調査も学術研究も、
   この対応付けの手法を開示していない。ここが空白であり中心的な貢献になる。
2. **事実と推察を分離して提示する。** DNSレコードの生値（fact）と、そこから導いた
   メール基盤・製品名（inference）を、スキーマレベルで分離する。
3. **日米を同一手法で比較する。** 共通中間分類（12業種）を介して、母集団定義の異なる
   企業群を同じ軸で並べる。

## 現在の実装状況

Sprint 1（骨格と P1・日本側）まで実装済み。

| フェーズ | 内容 | 状態 |
|---|---|---|
| P1 母集団確定 | 企業リストの取得と identity 付与 | **実装済**（日本・EDINET 経路） |
| P2 ドメイン候補生成 | 企業から関連ドメイン群を展開 | 未実装（Sprint 2） |
| P3 メールドメイン確定 | 候補から送信ドメインを絞り確度付与 | 未実装（Sprint 2） |
| P4 DNS計測 | MX/TXT/SPF/DKIM/DMARC 等の生取得 | 未実装（Sprint 3） |
| P5 パース | 生レスポンスの構造化と仕様準拠の解釈 | 未実装（Sprint 4） |
| P6 推察 | メール基盤・製品の推定、パーク分類 | 未実装（Sprint 5） |
| P7 集計 | 全社統計・業種別集計・前月差分 | 未実装（Sprint 6） |
| P8 公開 | 静的サイト生成とデプロイ | 未実装（Sprint 7） |

未実装のフェーズは、実行すると「どのスプリントで実装予定か」を添えて停止する。
空の出力を作って下流に「0件だった」と誤解させないため。

運用コンソールは画面1（パイプライン全景）と画面2（フェーズ実行）まで。

## セットアップ

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[console,dev]"
cp .env.example .env    # 認証情報を入れる。無くても P1 は動く
```

## 使い方

```bash
# 八工程の CLI。--run は実行ID（既定は当月 YYYY-MM）
mailauth p1-population --config configs/populations/jp-all-listed.yaml --run 2026-08

# 開発中の高速反復
mailauth p1-population --run 2026-08 --limit 100 --dry-run

# ネットワークに出ずにローカルのコードリストで動かす
mailauth p1-population --run 2026-08 --source-file path/to/Edinetcode.zip

# 実行状況
mailauth status --run 2026-08
mailauth populations
```

運用コンソール（ローカル専用・認証なし）:

```bash
uvicorn console.backend.main:app --reload --port 8000
cd console/frontend && npm install && npm run dev   # http://localhost:5173
```

`npm run build` しておけば、`http://localhost:8000` だけでコンソールが開く。

## 母集団

| id | 内容 | 状態 |
|---|---|---|
| `jp-all-listed` | 証券コードを持つ EDINET 提出者すべて（実測 約3,830社） | 実装済 |
| `jp-prime` | 東証プライム | 設定はあるが、**市場区分で絞れない**（下記） |
| `jp-standard` / `jp-growth` | 東証スタンダード / グロース | 同上 |
| `jp-nikkei225` | 日経225 | 構成銘柄リストの利用条件が未確認のため無効 |
| `us-fortune500` / `global500` | Fortune 500 / Global 500 | Sprint 1.5 |

### 市場区分について（既知の制約）

DESIGN.md は `segment_source: none`（JPX 非依存）を定めているが、EDINETコードリストは
市場区分を持たない。したがって `jp-prime` を実行しても実際に取れるのは
**全上場企業**であり、受け入れ基準の 1,450〜1,650 社には収まらない。
P1 はこの状態を `MARKET_SEGMENT_UNAVAILABLE` として manifest に記録する。

当面の運用は次のいずれか。

- `jp-all-listed` を使う（分母が広くなるが、JPX 非依存は保たれる）
- JPX 由来でない証券コード一覧を用意し、`market_filter.segment_allowlist` に指定する

## 設計上の約束

実装中に判断に迷ったら DESIGN.md 第2章の7原則に戻ること。特に次の3つは
コードに直接効いている。

- **原則1 生データは不変** ── bronze は書いたら変えない。パーサのバグは silver 以降の再生成で直す
- **原則4 何件処理して何件失敗したかを必ず記録する** ── 全フェーズが `_manifest.json` を書く。例外で落ちても書く
- **原則5 「取れなかった」と「無かった」を区別する** ── `observed` と `record_present` を別カラムで持つ

## データの置き場所

| 層 | 形式 | 保存先 | Git |
|---|---|---|---|
| bronze | JSON Lines (zstd) | `data/runs/<run_id>/` → R2 | **入れない** |
| silver | Parquet | `data/runs/<run_id>/` → R2 | **入れない** |
| gold | Parquet | `gold/month=YYYY-MM/` | 入れる |

bronze / silver を Git に入れないのは、git-scraping パターンの既知の弱点である
履歴肥大化を避けるため。

## 法務・倫理上、変えてはいけないこと

以下は `tests/test_compliance.py` が CI で機械的に検査している。

- **JPX の `data_j.xls` は使わない。** 非商用であっても方針として使わない。EDINET で代替できる
- **fortune.com はスクレイピングしない。** 会社名リストは Wikidata（CC0）/ SEC EDGAR から再構築する
- **DKIM 辞書 L3（Tatang、GPL-3.0）は無効のまま。** コピーレフト波及の法務確認が済むまで
- **認証情報をハードコードしない。** `.env` から読む
- **出典表記を成果物に含める。** 公共データ利用規約1.0 / 政府標準利用規約2.0 の要件

計測の作法（SPF include と MX ホストの解決結果のキャッシュ、ドメイン順のシャッフル、
計測の説明ページと連絡先の公開）は P4 実装時に必ず入れること。

## 出典

- 出典：EDINET（金融庁）
- 出典：国税庁法人番号公表サイト（国税庁）
- 出典：経済産業省 gBizINFO

いずれも公共データ利用規約1.0 / 政府標準利用規約2.0 に基づき、加工して利用している。

## 引き継ぎ計画

本プロジェクトは個人研究として個人アカウントで運営している。運営者が続けられなくなった
時点でプロジェクトが死ぬ（バス係数1）ことが最大のリスクであり、DESIGN.md 1.4 はこれを
明示的なリスクとして扱っている。引き継ぐ人のために次を守る。

1. **コードは OSS として公開し続ける。** ライセンスは Apache-2.0
2. **gold は CC0 で公開する。** 誰でも引き継いで分析を続けられるように
   （OpenINTEL 由来データを混ぜる場合は CC BY-NC-SA の継承条項が波及しうるため、
   別ファイル・別ライセンスで分離して配布すること）
3. **認証情報の取得元を `.env.example` に明記する。** どこで再発行できるかが分かること
4. **設計仕様（DESIGN.md）とリサーチ（research-dossier）をリポジトリに置き続ける。**
   「なぜこうなっているか」が失われると、引き継いだ人は同じ調査をやり直すことになる
5. **Zenodo で DOI を取る**（Sprint 10）。学術的な参照可能性を確保する

引き継ぎを希望する場合、または本システムの計測対象から外れたい場合は、
リポジトリの Issue で連絡してほしい。

## 開発

```bash
pytest -q                       # ネットワークには一切出ない
ruff check src console tests
cd console/frontend && npm run build
```

テストが外部 API に依存していないのは意図的である。EDINET も gBizINFO も国税庁も、
フィクスチャか「認証情報が無いのでスキップした」という記録で賄えるようにしてある。
