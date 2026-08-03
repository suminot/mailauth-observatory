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
| P2 ドメイン候補生成 | 企業から関連ドメイン群を展開 | **実装済** |
| P3 メールドメイン確定 | 候補から送信ドメインを絞り確度付与 | **実装済** |
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

mailauth p2-candidates --run 2026-08   # 候補ドメインを展開（再現率優先）
mailauth p3-domains    --run 2026-08   # 送信ドメインを絞り確度を付ける

# 実行状況
mailauth status --run 2026-08
mailauth populations

# 同じ計測結果を別の軸で見る（下記「ビュー」）
mailauth views
mailauth view --run 2026-08 --view jp-all   --by common12   # 全上場 × 業種軸
mailauth view --run 2026-08 --view jp-prime --by common12   # プライムのみ
mailauth view --run 2026-08 --view jp-all   --by segment    # 市場区分の内訳
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

## ビュー ── 計測は一度、見方は切り替える

「全上場を業種軸で見たい」ときと「プライムだけ見たい」ときがある。これを
母集団を変えて計測し直す形で実現すると、計測を何度も回すことになり、月次の
比較もできなくなる。そこで **計測は全上場で一度だけ回し、見るときに絞る**。

- `configs/populations/` = 何を測るか
- `configs/views/` = どう見るか

```bash
mailauth view --run 2026-08 --view jp-all   --by common12   # 全上場 × 共通12業種
mailauth view --run 2026-08 --view jp-all   --by industry   # 全上場 × EDINET33
mailauth view --run 2026-08 --view jp-prime --by common12   # プライム × 共通12業種
mailauth view --run 2026-08 --view jp-all   --by segment    # 区分ごとの社数
```

コンソールの「ビュー切り替え」タブでも同じことができる（数字は CLI と一致する。
同じ `mailauth.views` を呼んでいる）。

### 市場区分は、対応表を与えたときだけ付く

EDINETコードリストは市場区分を持たない。JPX の `data_j.xls` は使わない方針なので、
区分は `configs/populations/_segments/` に対応表を置いたときにだけ付く。

```yaml
source:
  market_filter:
    segment_source: manual_csv
    segment_map: configs/populations/_segments/jp-tse.csv
```

これは**計測対象を絞る設定ではない**。全上場を測ったまま各社にラベルを付けるだけで、
絞るのはビューの役目。書式と作り方は
[`configs/populations/_segments/README.md`](configs/populations/_segments/README.md) を参照。

対応表が無い状態で `jp-prime` を見ると総数は0になるが、これは「該当企業が無い」
ではなく「区分を判定できない」である。CLI もコンソールもその区別を警告として
明示する（原則5）。**総数0を該当なしと読まないこと。**

区分の自動生成（各社の有価証券報告書 表紙【上場金融商品取引所】を XBRL から読む）は
将来のスプリントで実装する。Wikidata は実測したが、東証上場で証券コードを持つ項目が
1件しかなく、区分の項目も存在しないため使えない。

なお `market_segment` 列は**内部の集計軸専用**であり、公開成果物には出さない
（DESIGN.md P1「市場区分は内部フィルタに留め、成果物には出さない」）。

## ドメイン同定（P2・P3）

DESIGN.md が「本プロジェクト最大の難所」「システムの中核」とする部分。

**P2 は広く拾う。** 四つの経路で候補を展開し、ここでは絞らない。絞ると P3 で
拾い直せないため、再現率を優先する。

| 経路 | 内容 |
|---|---|
| `official_url` | P1 が gBizINFO から取った公式サイトのドメイン |
| `ct_log` | crt.sh の SAN から eTLD+1 を抽出。1ドメインに数千件返るので apex に丸めて重複排除 |
| `spf_redirect` | SPF の `redirect=` が別ドメインを指していれば候補に |
| `dmarc_rua` | `_dmarc` の rua 宛先が自社ドメインなら候補に |
| `manual` | 手動辞書。グループ会社・事業ブランド用（`configs/domains/`） |

rua が第三者のレポート処理サービス（`dmarc25.jp` 等）を指している場合は候補にしない。
入れると1つのベンダードメインが数百社に紐づき、他社のドメインを計測してしまう。
判定は `configs/vendors/dmarc_rua_vendors.yaml` を使う。

**P3 は絞って確度を付ける。**

| 確度 | 条件 |
|---|---|
| `confirmed` | MX 実在 + From整合する SPF か DKIM + 独立ソースの裏付け1件以上 |
| `likely` | MX 実在で SPF/DMARC はあるが独立裏付けが不足 |
| `parked` | MX 不在 + SPF で `-all`（送信全否定）を宣言 |
| `unknown` | MX 不在、または一次実証なし |

「独立した裏付け」は DNS 由来でない発見経路（`official_url` / `ct_log` / `manual`）を
数える。`spf_redirect` と `dmarc_rua` は DNS 由来なので、DNS 一次実証からは独立でない。
定義は `configs/candidates.yaml` の `confidence.independent_methods`。

**MX が無くても SPF がある場合を正しく扱う。** Czybik et al.（IMC 2023）は MX の無い
ドメインの 10.4% が SPF を持ち、うち 53.1% が `-all`/`~all` だと報告している。これは
設定漏れではなく意図的な送信禁止宣言なので、`parked` として分離する。Null MX
（RFC 7505 の `0 .`）も同様に「受け取らない」の明示的な宣言であり、`mx_exists` には数えない。

確度に応じて P4 の計測の深さ（`measure_tier`）を決める。A がフル計測、C が簡易計測。
送信していないドメインに DKIM セレクタを50個投げても検出されないし、権威DNSへの
負荷という点で作法が悪い。

### TCP/53 が必要

TXT レコードが多いドメインでは UDP 応答が 512 バイトを超えて truncated になり、
TCP/53 への切り替えが必要になる。**TCP/53 を通さない経路では大企業の SPF を
一切観測できない。** その場合 P3 は該当ドメインを `observed=false`（取れなかった）
として計測対象から外し、`TCP53_UNAVAILABLE` を警告する。「SPF が無い」と
読まないこと。

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
