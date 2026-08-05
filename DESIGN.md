# メール認証月次計測システム 設計仕様書

**版**: v0.2 (2026-08-02)
**対象読者**: 実装担当（Claude Code を含む）
**前提資料**: 「メール認証月次計測システム 設計前リサーチ統合ドシエ」（DR-01〜DR-18 の統合）

### v0.1 からの変更点

| # | 変更 | 影響範囲 |
|---|---|---|
| 1 | 運営形態を**個人研究・個人アカウント**に確定 | 全体。非商用が明確になりライセンス制約が緩和 |
| 2 | 母集団を**日米同時**、Fortune 500 (US) と Global 500 の**両方**に拡大 | P1。identity 構築が三系統に |
| 3 | ドメイン展開を**網羅展開（最大30,000）**に確定 | P2〜P4。クエリ量が約10倍 |
| 4 | P4 に**階層別計測**を導入（フル計測とパーク判定に分離） | P4。上限6時間への対策 |
| 5 | **パークドメイン分類**を指標として追加 | P3・P6・P7。本システム固有の差別化指標 |
| 6 | DKIM 辞書は **L1/L2 まで**。L3 は UI に「開発予定」と表示 | P4・コンソール |
| 7 | **DANE は計測する**。主要指標にはしないが欧州比較軸として保持 | P4・P7 |
| 8 | 市場区分は**内部フィルタに限定**（JPX 非依存を維持） | P1 |
| 9 | 第2層（個社名付き）と通知キャンペーンを**実施する前提**でロードマップに組み込み | Sprint 9・10 |
| 10 | バックフィルは**当面やらない**。ただし OpenINTEL は非商用で利用可能に | P7・未解決事項 |

---

## 0. この文書の位置づけ

リサーチ統合ドシエが「何が分かっているか」を扱うのに対し、本書は「何を作るか」を扱う。

v0.2 の時点で、着手に必要な設計判断はすべて確定済みである（第11章の「決定済み」表を参照）。残る未解決事項は6件あるが、いずれも実装と並行して解決できるもので、Sprint 1 の着手を妨げない。

本書の記述粒度は「Claude Code に本書を読ませれば、追加の質問なしにモジュールの雛形が生成できる」ことを目標とする。

---

## 1. システムの目的と非目的

### 1.1 目的

Fortune 500 および東証プライム上場企業を対象に、各社が実際にメール送信に用いているドメインを同定し、そのメール認証設定（SPF / DKIM / DMARC / BIMI / MTA-STS / TLS-RPT / DNSSEC）と利用メール基盤を月次で計測し、時系列として蓄積・公開する。

このシステムが提供する固有の価値は三つある。

1. **企業とドメインの対応を、確度を明示して透明に公開する。** 既存の商用調査（TwoFive、Valimail、Proofpoint）も学術研究も、この対応付けの手法を開示していない。ここが唯一の空白であり、本システムの中心的な貢献になる。
2. **事実と推察を分離して提示する。** DNSレコードの生値（事実）と、そこから導いたメール基盤・製品名（推察）を、スキーマレベルで分離し、UI上も分離表示する。
3. **日米を同一手法で比較する。** 共通中間分類（12業種）を介して、母集団定義の異なる日米企業群を同じ軸で並べる。

### 1.2 非目的

以下は本システムの範囲外とする。範囲を絞ることが、10年続けるための前提になる。

- メールの実受信・実送信を伴う計測（受信側テレメトリ、ARC、DKIM実署名率）
- 企業の総合的なセキュリティ評価やスコアリング、格付け、順位付け
- 侵入的なスキャン、認証を要するアクセス、ポートスキャン
- リアルタイム監視、アラート配信
- 個人情報（個人名を含むメールアドレス）の収集・公開

### 1.3 規模の前提

| 項目 | 値 |
|---|---|
| 対象企業 | 東証プライム 約1,550社 + Fortune 500 (US) 500社 + Fortune Global 500 500社（重複あり） |
| 実効企業数 | 約2,300〜2,400社（日本企業の重複排除後） |
| 対象ドメイン | **最大 30,000**（網羅展開） |
| うちフル計測対象 | 約8,000（confirmed / likely） |
| うち簡易計測対象 | 約22,000（parked / unknown） |
| DNSクエリ | **約56万/月**（階層別計測による削減後） |
| 実行時間 | 約1.9時間 / 80 QPS（GitHub Actions のジョブ上限6時間に対し余裕あり） |
| 実行頻度 | 月次（当初はアドホック手動） |
| 月額コスト目標 | 0円 |

### 1.4 運営形態と、そこから導かれる制約

本システムは**隅野貴裕の個人研究**として、個人アカウントで運営する。この決定は次の帰結を持つ。

**緩和される制約**

| 制約 | 変化 |
|---|---|
| OpenINTEL（CC BY-NC-SA 4.0） | **利用可能になる。**非商用が明確なため。SPF と MX は2016年まで遡及できる |
| Tranco 標準リスト（構成に CC BY-NC の Cloudflare Radar を含む） | 利用可能 |
| JPX の「商用目的によるデータ収集及び二次利用」禁止 | 射程外の可能性が高い。ただし後述のとおり方針は変えない |

**強化される制約**

| リスク | 内容 | 対策 |
|---|---|---|
| バス係数1 | 運営者が続けられなくなればプロジェクトは死ぬ。学術成果物の約20%は発表4年後にアクセス不能になる（Frachtenberg 2022） | bronze の CC0 公開、コードの OSS 化、README への引き継ぎ計画明記 |
| 個人アカウント資産への依存 | GitHub / Cloudflare の認証情報・課金・ドメインが個人名義 | 認証情報の文書化。将来の組織移管を想定した命名 |
| 公益目的の立証力 | 第2層（個社名付き）を個人名義で出す場合、名誉毀損の抗弁における「公益目的」の立証が組織運営より弱くなる | 方法論・コード・データの完全公開、事前通知、訂正窓口の常設 |
| 業務上の立場との結びつき | 運営者がサイバーセキュリティコンサルタントであることは公知。個人研究として企業を名指し評価する行為が業務と結びつけて受け取られる余地がある | サイトに所属と独立性を明記。営業要素を一切含めない |

**方針として変えないもの**

JPX の `data_j.xls` は、非商用であっても使わない。理由は二つある。EDINET で完全に代替できること、そして「グレーゾーンに依存しない」という設計上の潔癖さが、個人研究の信頼性を支えるからである。同様に fortune.com のスクレイピングも行わない。

---

## 2. 設計原則

実装中に判断に迷ったら、この7原則に立ち返ること。

### 原則1 ── 生データは不変

DNSレスポンスの生値は JSON Lines で bronze 層に書き込み、以後**一切変更しない**。パーサや判定ロジックにバグが見つかっても、bronze は触らず、silver 以降を再生成する。これにより、1年後にフィンガープリント辞書を拡充したとき、過去12か月分を遡って再判定できる。

### 原則2 ── 事実と推察を混ぜない

`fact` テーブルには DNS から直接読み取れる値のみを入れる。「Proofpoint を使っている」は fact ではなく inference である。inference には必ず confidence と、根拠となった fact への参照（evidence）を持たせる。

この分離は技術的な整理であると同時に、名誉毀損リスクへの防御でもある。「DMARC が none である」は検証可能な事実であり真実性の立証が容易だが、「なりすましリスクが高い」は意見論評型に寄る。

### 原則3 ── 各フェーズは単独で動く

すべてのフェーズは、前後のフェーズを知らずに、ファイルを入力にファイルを出力する。フェーズ間の結合はファイルパスの規約のみ。これにより、任意のフェーズだけを再実行でき、任意のフェーズの出力を人間が検査できる。

### 原則4 ── 何件処理して何件失敗したかを必ず記録する

すべてのフェーズは実行の最後に `_manifest.json` を書く。入力件数、成功、失敗、スキップ、失敗の理由別内訳を含む。この manifest が運用コンソールの表示元になる。

### 原則5 ── 「取れなかった」と「無かった」を区別する

DNS計測で最も重要な設計判断。`observed`（クエリ自体が成功したか）と `record_present`（レコードが存在したか）を別カラムで持つ。時系列で「消滅」と判定するには、最低2連続の成功観測での不在を要求する。

### 原則6 ── 冪等かつ再実行可能

同じ入力に同じフェーズを2回走らせたら、同じ出力になる。途中で落ちても、最初からやり直せる。部分的失敗があっても、成功分は保存される。

### 原則7 ── 設定はコード外に出す

母集団の選択、DKIMセレクタ辞書、フィンガープリント規則、業種マッピングは、すべて YAML / CSV としてリポジトリに置き、Git で版管理する。コードを触らずに対象や判定ルールを変えられること。

---

## 3. 全体像

### 3.1 二つのアプリケーション

本システムは性格の異なる二つのアプリからなる。混同しないこと。

**運用コンソール（`console/`）**
開発・運用者だけが使う内部ツール。ローカルで起動する。各フェーズをボタンで実行し、工程ごとのメトリクスを表示し、個々のレコードを検査し、複数の取得手法を比較する。**開発初期はこれが主戦場**になる。

**公開サイト（`site/`）**
第三者向けの静的サイト。Cloudflare Pages に配信する。第1層（全社統計・業種別集計）は無条件公開、第2層（個社名付き）は Cloudflare Access で保護する。ロジックが固まってから着手する。

### 3.2 パイプラインの八工程

```
P1 母集団確定      企業リストの取得と identity 付与
      ↓ entities.parquet
P2 ドメイン候補生成  企業から関連ドメイン群を展開
      ↓ domain_candidates.parquet
P3 メールドメイン確定 候補から実際に送信に使われるものを絞り込み確度付与
      ↓ domains.parquet
P4 DNS計測         MX/TXT/SPF/DKIM/DMARC/周辺プロトコルの生取得
      ↓ bronze/*.jsonl.zst
P5 パース          生レスポンスの構造化と仕様準拠の解釈
      ↓ silver/facts.parquet
P6 推察            メール基盤・セキュリティ製品の推定
      ↓ silver/inferences.parquet
P7 集計            全社統計・業種別集計・前月差分
      ↓ gold/*.parquet
P8 公開            静的サイト生成とデプロイ
      ↓ Cloudflare Pages
```

各フェーズは独立した CLI サブコマンドとして実装する。

```bash
mailauth p1-population --config configs/populations/jp-prime.yaml --run 2026-08
mailauth p2-candidates --run 2026-08
mailauth p3-domains    --run 2026-08
mailauth p4-measure    --run 2026-08 --method zdns
mailauth p5-parse      --run 2026-08
mailauth p6-infer      --run 2026-08
mailauth p7-aggregate  --run 2026-08
mailauth p8-publish    --run 2026-08
```

`--run` は実行ID（既定は `YYYY-MM`）。すべての中間成果物はこの ID の下に置かれる。

---

## 4. リポジトリ構成

```
mailauth-observatory/
├── README.md
├── pyproject.toml
├── configs/
│   ├── populations/            # 母集団の定義
│   │   ├── jp-prime.yaml
│   │   ├── jp-standard.yaml
│   │   ├── jp-growth.yaml
│   │   ├── jp-nikkei225.yaml
│   │   ├── us-fortune500.yaml
│   │   ├── global500.yaml
│   │   └── _sparql/
│   ├── measure.yaml            # DNS計測のパラメータ
│   ├── dkim_selectors/         # DKIMセレクタ辞書（三層）
│   │   ├── l1_core.txt         #   必須40〜60
│   │   ├── l2_provider.yaml    #   プロバイダ別（動的付与）
│   │   └── l3_extended.txt     #   Tatang辞書（GPL-3.0、要法務確認）
│   ├── fingerprints/           # 基盤・製品の判定規則
│   │   ├── platforms.yaml
│   │   ├── security_gw.yaml
│   │   ├── esp.yaml
│   │   └── verification_txt.yaml
│   ├── industry/               # 業種分類の写像
│   │   ├── edinet33_to_common12.csv
│   │   ├── sic_to_common12.csv
│   │   └── common12.yaml
│   └── vendors/
│       └── dmarc_rua_vendors.yaml   # rua宛先→ベンダー推定
├── src/mailauth/
│   ├── cli.py
│   ├── contracts.py            # 全フェーズのI/Oスキーマ定義
│   ├── manifest.py             # 工程メトリクスの記録
│   ├── p1_population/
│   ├── p2_candidates/
│   ├── p3_domains/
│   ├── p4_measure/
│   │   ├── backends/           # zdns / dnsx / dnspython / securitytrails
│   │   └── resolver.py
│   ├── p5_parse/
│   │   ├── spf.py
│   │   ├── dmarc.py            # spec_version 二重計算
│   │   ├── dkim.py
│   │   └── extras.py           # BIMI/MTA-STS/TLS-RPT/DANE/DNSSEC
│   ├── p6_infer/
│   ├── p7_aggregate/
│   └── p8_publish/
├── console/                    # 運用コンソール
│   ├── backend/                # FastAPI
│   └── frontend/               # Vite + React + TypeScript
├── site/                       # 公開サイト（Observable Framework）
├── data/                       # ローカル作業領域（.gitignore）
│   └── runs/<run_id>/
├── gold/                       # 集計結果のみGitコミット
│   └── month=YYYY-MM/
├── tests/
└── .github/workflows/
```

**Git にコミットするもの**: ソース、設定、`gold/` の軽量 Parquet。
**Git にコミットしないもの**: `data/runs/` 配下の bronze / silver。これらは Cloudflare R2 に退避する。

理由は、git-scraping パターンの既知の弱点である履歴肥大化の回避。ある事例では80MBのデータで4年運用後にリポジトリが1GB超に膨張している。

---

## 5. データ契約

### 5.1 実行ディレクトリの構造

```
data/runs/2026-08/
├── p1_population/
│   ├── entities.parquet
│   └── _manifest.json
├── p2_candidates/
│   ├── domain_candidates.parquet
│   └── _manifest.json
├── p3_domains/
│   ├── domains.parquet
│   └── _manifest.json
├── p4_measure/
│   ├── bronze/
│   │   ├── method=zdns/part-0000.jsonl.zst
│   │   └── method=dnspython/part-0000.jsonl.zst
│   └── _manifest.json
├── p5_parse/
│   ├── facts.parquet
│   └── _manifest.json
├── p6_infer/
│   ├── inferences.parquet
│   └── _manifest.json
└── p7_aggregate/
    ├── stats_overall.parquet
    ├── stats_by_sector.parquet
    ├── domain_detail.parquet
    └── _manifest.json
```

### 5.2 run manifest（全フェーズ共通）

すべてのフェーズがこの形式で `_manifest.json` を書く。運用コンソールはこれを読んで工程を可視化する。

```json
{
  "run_id": "2026-08",
  "phase": "p4_measure",
  "started_at": "2026-08-01T02:00:00Z",
  "finished_at": "2026-08-01T02:41:33Z",
  "duration_sec": 2493,
  "status": "success",
  "tool_versions": {"zdns": "v1.1.0", "mailauth": "0.1.0"},
  "config_hash": "sha256:...",
  "counts": {
    "input": 2847,
    "success": 2801,
    "failed": 31,
    "skipped": 15
  },
  "failure_breakdown": {
    "SERVFAIL": 12,
    "TIMEOUT": 14,
    "REFUSED": 3,
    "resolver_exhausted": 2
  },
  "warnings": [
    {"code": "WILDCARD_DNS", "count": 7,
     "sample": ["example.co.jp"]}
  ],
  "outputs": [
    {"path": "bronze/method=zdns/part-0000.jsonl.zst",
     "records": 118432, "bytes": 24118293}
  ]
}
```

`status` は `success` / `partial` / `failed` の三値。`partial` は一部失敗したが出力は生成された状態。

### 5.3 三層データモデル

| 層 | 形式 | 可変性 | 保存先 | 内容 |
|---|---|---|---|---|
| **bronze** | JSON Lines (zstd) | 不変・追記のみ | R2 | DNSレスポンスの生値 |
| **silver** | Parquet | 再生成可能 | R2 | 正規化された fact と inference |
| **gold** | Parquet | 再生成可能 | Git + R2 | 集計値・時系列 |

silver と gold は bronze から完全に再生成できなければならない。これが原則1の実質的な意味である。

---

## 6. フェーズ仕様

以降、各フェーズについて 目的 / 入力 / 処理 / 出力 / メトリクス / 受け入れ基準 を定義する。

---

## P1 ── 母集団確定

### 目的
対象企業のリストを取得し、一意識別子と業種分類を付与して保全する。

### 入力
`configs/populations/<name>.yaml`

### 設定ファイルの形式

```yaml
# configs/populations/jp-prime.yaml
id: jp-prime
label: 東証プライム市場上場企業
country: JP
enabled: true

source:
  # 日本側の identity は政府オープンデータで構築する
  # JPX の data_j.xls は商用二次利用が規約で禁止のため使わない
  primary: edinet_code_list
  market_filter:
    # 市場区分の判定方法。JPX に依存しない手段を第一候補とする
    method: securities_code_presence   # 証券コードが存在＝上場
    # 決定: 市場区分は内部フィルタに留め、成果物には出さない
    # JPX への依存を避けるため segment_source は none で固定する
    segment_source: none
  enrich:
    - houjin_bangou      # 国税庁：商号・所在地の裏取り
    - gbizinfo           # company_url の取得
  industry:
    primary_scheme: EDINET33
    common_mapping: configs/industry/edinet33_to_common12.csv

refresh:
  # EDINETコードリストは月次更新
  cadence: monthly
  # 取得はAPI経由（スクレイピング禁止）
  method: api

attribution:
  - "出典：EDINET（金融庁）"
  - "出典：国税庁法人番号公表サイト（国税庁）"
  - "出典：経済産業省gBizINFO"
```

```yaml
# configs/populations/us-fortune500.yaml
id: us-fortune500
label: Fortune 500 (US)
country: US
enabled: true

source:
  # fortune.com はスクレイピングと二次利用を規約で禁止
  # 会社名リストは CC0 / パブリックドメインのソースから再構築する
  primary: wikidata
  wikidata_query: configs/populations/_sparql/fortune500.rq
  fallback: wikipedia          # CC BY-SA 4.0（帰属表示が必要）
  enrich:
    - sec_edgar_cik            # company_tickers.json でCIK付与
    - sec_edgar_submissions    # website と SIC を取得
    - gleif_lei
  industry:
    primary_scheme: SIC
    common_mapping: configs/industry/sic_to_common12.csv

refresh:
  # Fortune 500(US) は毎年6月上旬発表、2024会計年度基準
  cadence: annual
  announce_month: 6
  recheck_only_on_announce: true

constraints:
  # 順位と売上高は成果物に含めない（Fortune の編集著作物であるため）
  exclude_fields: [rank, revenue]

attribution:
  - "Source: Wikidata (CC0)"
  - "Source: U.S. SEC EDGAR (public domain)"
  - "Source: GLEIF"
```

```yaml
# configs/populations/global500.yaml
id: global500
label: Fortune Global 500
country: MULTI                 # 単一国に紐づかない
enabled: true

source:
  primary: wikidata
  wikidata_query: configs/populations/_sparql/global500.rq
  fallback: wikipedia

  # 重要: SEC EDGAR は米国提出者しかカバーしない。
  # Global 500 は2025年版で米国138社・大中華圏130社という構成のため、
  # 全世界をカバーする GLEIF LEI を identity の主キーにする。
  identity_primary_key: lei

  enrich:
    - gleif                      # 全世界。主キー
    - sec_edgar_cik              # 米国企業のみ。あれば付与
    - houjin_bangou              # 日本企業のみ。あれば付与
    - edinet_code                # 日本企業のみ

  industry:
    # 国ごとに一次分類が異なる。共通12分類で吸収する
    primary_scheme_by_country:
      US: SIC
      JP: EDINET33
      default: none              # 一次分類なし。common12 のみ
    common_mapping_fallback: manual   # 手動マッピングCSV

refresh:
  # Global 500 は7月下旬〜8月上旬発表
  # ランキングは3月31日までに終了する各社の会計年度の総収益ベース
  cadence: annual
  announce_month: 7
  recheck_only_on_announce: true

constraints:
  exclude_fields: [rank, revenue]

dedup:
  # 日本企業は jp-prime と、米国企業は us-fortune500 と重複する。
  # entity_id を LEI ベースで統一し、population_ids を配列で持つ。
  strategy: merge_by_lei
  fallback_keys: [houjin_bangou, cik, name_normalized]

attribution:
  - "Source: Wikidata (CC0)"
  - "Source: GLEIF"
```

### 処理

1. `source.primary` に応じてリストを取得
   - `edinet_code_list`: EDINET の `EdinetcodeDlInfo.csv`（cp932、13列）を API 経由でダウンロード。証券コード（12列目）が空でないレコードを上場企業として抽出
   - `wikidata`: SPARQL でクエリ
2. `enrich` を順に適用して identity を補完
3. 業種を一次分類（EDINET33 / SIC）と二次分類（共通12分類）の両方で付与
4. 前月の `entities.parquet` と突合し、新規・消滅・変更を検出
5. `entities.parquet` を書き出し

### 出力スキーマ

```
entity_id            STRING   PK  LEI があれば lei:XXXX、無ければ jp:法人番号 / us:CIK
run_id               STRING
country              STRING   ISO 3166-1 alpha-2
population_ids       ARRAY<STRING>  複数母集団に属しうる（例 ["jp-prime","global500"]）
is_duplicate_of      STRING   重複排除で吸収された場合の統合先 entity_id
name                 STRING
name_en              STRING
name_normalized      STRING   NFKC正規化＋法人格語除去（名寄せ用）

houjin_bangou        STRING   13桁（JP）
edinet_code          STRING   （JP）
securities_code      STRING   4桁に正規化（EDINETの5桁から先頭4桁）
cik                  STRING   10桁ゼロ埋め（US）
lei                  STRING
ticker               STRING

official_url         STRING   gBizINFO company_url / SEC website
official_domain      STRING   official_url を eTLD+1 で正規化

industry_scheme      STRING   EDINET33 | SIC
industry_code        STRING
industry_label       STRING
common12_code        STRING   1〜12
common12_label       STRING
industry_map_version STRING

first_seen_month     DATE
last_seen_month      DATE
status               STRING   active | delisted | merged | renamed
change_note          STRING
```

### メトリクス
```
input:   ソースから取得した生の件数
success: identity が確定した件数
failed:  法人番号 or CIK が付かなかった件数
skipped: status != active

breakdown:
  official_url_missing:  official_url が取得できなかった件数
  industry_missing:      業種が付かなかった件数
  new_entities:          前月比 新規
  delisted_entities:     前月比 消滅
  renamed_entities:      社名変更
```

### 受け入れ基準
- 東証プライムで 1,450〜1,650 件が取得できること（2026年時点の実勢は約1,552社）
- Fortune 500 (US) と Global 500 がそれぞれ 480〜500 件取得できること
- 重複排除後の実効企業数が 2,250〜2,450 の範囲に入ること
- `houjin_bangou` の欠損率が 1% 未満（日本企業）
- `lei` の欠損率が 15% 未満（Global 500。非上場・相互会社等は LEI を持たない場合がある）
- `official_url` の欠損率が 10% 未満（gBizINFO の収録率に依存）
- 業種（common12）の欠損率が 5% 未満
- JPX の `data_j.xls` を一切参照していないこと（コード検査で確認）
- fortune.com へのリクエストが発生していないこと（同上）

### 実装メモ
- EDINET API は Subscription-Key が全リクエストで必須。`.env` から読む
- SEC は User-Agent ヘッダ必須（無しは403）、10 req/s 未満を厳守
- 社名の名寄せは NFKC 正規化 → 法人格語（株式会社、Inc.、Corp. 等）除去 → 空白正規化の順
- 証券コードは EDINET が5桁（末尾0）、一般が4桁。突合時は先頭4桁で

---

## P2 ── ドメイン候補生成

### 目的
企業1社に対し、その企業が保有している可能性のあるドメイン群を展開する。ここではまだ絞り込まない。**再現率を優先**する。

### 入力
`p1_population/entities.parquet`

### 処理

`official_domain` を起点に、四つの経路で候補を追加する。

1. **Certificate Transparency**: `crt.sh` で `%.<official_domain>` と組織名を検索し、SAN から FQDN を抽出。eTLD+1 に正規化して候補に追加
2. **SPF redirect / include の追跡**: `official_domain` の SPF を引き、`redirect=` が別の組織ドメインを指していれば候補に追加
3. **DMARC rua 宛先**: `_dmarc.<official_domain>` の rua 宛先ドメインが自社ドメインなら候補に追加
4. **既知の関連ドメイン辞書**: 手動メンテナンスの CSV。グループ会社や事業ブランドのドメインを人手で追加できる経路を必ず用意する

### 出力スキーマ

```
candidate_id      STRING  PK
entity_id         STRING  FK
run_id            STRING
domain            STRING  eTLD+1 に正規化
discovery_method  STRING  official_url | ct_log | spf_redirect | dmarc_rua | manual
discovered_at     TIMESTAMP
source_detail     STRING  crt.sh の証明書IDなど
is_apex           BOOLEAN
```

### メトリクス
```
input:  entities の件数
output: 候補ドメインの総数
breakdown:
  by_discovery_method: {official_url: N, ct_log: N, ...}
  domains_per_entity:  {p50: N, p90: N, max: N}
  entities_with_zero_candidates: N   # official_url すら無い企業
```

### 受け入れ基準
- 1社あたり候補ドメイン数の中央値が 1〜5、p90 が 50 以下
- 候補ゼロの企業が全体の 5% 未満
- 同一ドメインが複数企業に紐づく場合、警告として manifest に記録されること

### 実装メモ
- CT ログは1ドメインあたり数百〜数千の証明書を返すことがある。eTLD+1 に正規化してから重複排除しないと候補が爆発する
- `crt.sh` は重い。レート制限とリトライを必ず入れ、結果は run 単位でキャッシュする
- **1社平均41ドメイン**（TwoFive の日経225調査が225社→9,301ドメイン）という実勢がある。候補が数十件出るのは異常ではない

### 展開の深さ（決定済み）

**網羅展開を採用する。上限 30,000 ドメイン。**

CT ログ・SPF redirect・DMARC rua・手動辞書の四経路をすべて使い、企業が保有するドメインを可能な限り拾う。ただし主ドメインと関連ドメインは `domain_role` で区別し、集計時に両方の数字を出す。

この判断の根拠は、TwoFive が日経225の225社に対して9,301ドメイン（1社平均41）を対象にしているという実勢と、同社が「企業数ベース69.8%」と「ドメインベース18.5%」という51ポイントの乖離を公表している事実にある。この乖離こそが最も情報量のある指標であり、両方を出すことが誠実であり差別化になる。

さらに本システムでは、網羅展開した非送信ドメインを**パークドメイン分類**（P6 参照）にかけることで、「送信ドメインの強制率」と「非送信ドメインの防御率」を分離して提示する。これは既存のどの調査も出していない数字である。

### ドメイン階層の割り当て

30,000 ドメインすべてにフル計測をかけると GitHub Actions の6時間上限に触れる。P3 で階層を割り当て、P4 で計測の深さを変える。

| 階層 | 割当条件 | 想定件数 | P4 での計測 |
|---|---|---|---|
| **A** | confidence が confirmed または likely | 約8,000 | フル（DKIM 50セレクタ、DANE 含む） |
| **C** | parked / unknown | 約22,000 | 簡易（MX / SPF / DMARC / Null MX のみ） |

階層 C にフル計測をかけない理由は二つある。実務上、送信していないドメインに DKIM セレクタを50個投げても検出されないこと。そして権威DNSへの負荷という点で作法が悪いこと。パークドメイン分類に必要なのは MX と SPF と DMARC だけなので、階層 C はこれで過不足ない。

---

## P3 ── メールドメイン確定

### 目的
候補ドメインのうち、実際にメール送信に使われているものを絞り込み、三段の確度フラグを付ける。**ここが本システムの中核**である。

### 入力
`p2_candidates/domain_candidates.parquet`

### 処理

各候補について軽量な DNS 一次実証を行う（本格計測は P4）。

1. MX の存在を確認
2. apex の TXT から SPF の存在を確認
3. `_dmarc` の存在を確認
4. From整合（候補ドメインと SPF/DMARC が同一 eTLD+1 か）を判定
5. 確度フラグを付与

### 確度フラグの判定ロジック

```python
def classify_confidence(mx_exists, spf_aligned, dkim_found,
                        dmarc_exists, independent_evidence_count):
    """
    Confirmed: MX実在 かつ From整合するSPFまたはDKIMを確認
               かつ 独立ソースでの裏付けが1件以上
    Likely:    MX実在 かつ SPF/DMARC存在 だが独立裏付けが不足
               または 子ドメイン/グループ会社ドメイン
    Unknown:   MX不在、または一次実証なし
    """
    if not mx_exists:
        # MX が無くても SPF で -all/~all を宣言していれば
        # 「意図的な送信禁止」であって設定漏れではない
        return "parked" if spf_hard_deny else "unknown"

    primary = spf_aligned or dkim_found
    if primary and independent_evidence_count >= 1:
        return "confirmed"
    if primary or dmarc_exists:
        return "likely"
    return "unknown"
```

### 証拠の優先順位

推定に用いる証拠の強さは次の順である。実装ではこの順に重み付けする。

```
DKIM CNAME  ≧  MX  >  SPF include  >  所有権確認TXT
```

- **DKIM CNAME**: 署名基盤。最も実基盤に近い
- **MX**: インバウンド経路。前段ゲートウェイを最も強く示す
- **SPF include**: アウトバウンド送信元
- **所有権確認TXT**: 過去または現在の利用サービス。最も弱い

### 出力スキーマ

```
domain_id           STRING  PK
entity_id           STRING  FK
run_id              STRING
domain              STRING
domain_role         STRING  primary | related | parked
confidence          STRING  confirmed | likely | unknown | parked

mx_exists           BOOLEAN
spf_exists          BOOLEAN
spf_aligned         BOOLEAN
dmarc_exists        BOOLEAN
dkim_found          BOOLEAN
evidence_count      INTEGER
evidence            JSON    根拠の配列

is_measured         BOOLEAN 次フェーズで計測対象とするか
measure_tier        STRING  A（フル）| C（簡易）
exclusion_reason    STRING  除外した場合の理由

-- パークドメイン判定の材料（P6 で使う）
null_mx             BOOLEAN RFC 7505 の Null MX（"0 ."）を検出
spf_hard_deny       BOOLEAN v=spf1 -all（送信全否定）
```

### メトリクス
```
input:  候補ドメイン数
output: is_measured=true の件数
breakdown:
  by_confidence: {confirmed: N, likely: N, unknown: N, parked: N}
  by_role:       {primary: N, related: N}
  entities_with_no_confirmed_domain: N   # 要注意。手動確認の候補
```

### 受け入れ基準
- `confirmed` が候補全体の 30% 以上
- `entities_with_no_confirmed_domain` が全企業の 15% 未満
- `parked`（MX無し + SPF hard deny）が正しく分離されていること

### 実装メモ

**MX が無くても SPF がある場合を正しく扱うこと。** Czybik et al.（IMC 2023）は、MXレコードのないドメインの10.4%がSPFレコードを返し、うち53.1%が `v=spf1 -all` または `~all`（送信全否定）であると報告している。これは設定漏れではなく意図的な送信禁止宣言である。MXフィルタを機械的に適用すると、この意図を見落とす。

**防衛的登録ドメインの分離。** Maroofi et al.（IEEE TNSM 2021）の識別アルゴリズムの考え方を、「悪性判定」ではなく「同一組織の正当な別ドメイン判定」に転用する。語彙的特徴と登録タイムスタンプで、公開データのみから高精度に分類できることが示されている（COMAR で95%、後継研究で97%）。

**確度の昇降格。** 月次で次の遷移を評価する。
- DMARC 集約レポートに実送信ソースが継続観測されれば `likely → confirmed`
- MX が90日連続で消失、または `p=reject` かつ送信実績ゼロなら `parked` へ降格

---

## P4 ── DNS計測

### 目的
確定したドメインについて、メール認証に関わる全レコードを取得し、生のまま bronze に保存する。

### 入力
`p3_domains/domains.parquet` のうち `is_measured=true`

### 取得するレコード（階層別）

P3 で割り当てた `measure_tier` に応じて計測の深さを変える。

| 対象 | クエリ | 階層A | 階層C |
|---|---|---|---|
| MX（Null MX 判定を含む） | `<domain>` MX | 1 | 1 |
| SPF | `<domain>` TXT | 1 | 1 |
| DMARC | `_dmarc.<domain>` TXT | 1 | 1 |
| DMARC サブドメイン | `_dmarc.<random>.<domain>` TXT | 1 | 1（np= の実効確認） |
| DKIM | `<selector>._domainkey.<domain>` TXT | 40〜50 | 0 |
| DKIM 対照 | `<random>._domainkey.<domain>` TXT | 1（偽陽性ガード） | 0 |
| DKIM ワイルドカード | `*._domainkey.<domain>` TXT | 1（失効鍵の検出） | 1 |
| MTA-STS | `_mta-sts.<domain>` TXT | 1 | 0 |
| TLS-RPT | `_smtp._tls.<domain>` TXT | 1 | 0 |
| BIMI | `default._bimi.<domain>` TXT | 1 | 0 |
| DNSSEC | DO/AD フラグ | 0（相乗り） | 0（相乗り） |
| DANE | `_25._tcp.<mx-host>` TLSA | 2〜9（MX二段） | 0（MX が無いため不要） |
| **小計** | | **約59** | **約5** |

### クエリ量と実行時間の試算

```
階層A  8,000 × 59 = 472,000
階層C 22,000 ×  5 = 110,000
                  ─────────
              計   582,000 クエリ/月

80 QPS で約 2.0 時間。GitHub Actions のジョブ上限6時間に対し十分な余裕。
全ドメインにフル計測をかけた場合は 30,000 × 59 = 1,770,000 で約6.1時間となり
上限に触れるため、階層分割は必須である。
```

### DANE の位置づけ

DANE（RFC 7672）は DNSSEC で署名した DNS に TLSA レコードを置き、送信側が MX ホストの証明書を検証する仕組みである。日米ではほぼ普及していない（Tranco 5.5M 中30ドメイン、日本の DANE 対応 MX ホストは36〜57件程度）。

しかし **Global 500 の追加により欧州企業が母集団に入るため、価値が変わった。**DANE はオランダとドイツに極端に偏って普及しており、日米欧を同一手法で並べたときに地域差が最も鮮明に出る指標のひとつになる。

- **計測する**: 階層A の MX ホストに対して TLSA を取得
- **成熟度ステージには入れる**（Stage 4 の到達条件のひとつ）
- **主要指標としては出さない**が、地域別クロス集計の軸として保持する
- **DNSSEC 署名の有無を必ず併記**する。DANE は DNSSEC が前提のため、「TLSA はあるが親ゾーンが未署名で実効しない」という誤設定を検出できる。これも他があまり出していない数字である

コスト増は階層A のみが対象なので約2万クエリ、全体582,000 に対して4%弱にとどまる。

### バックエンドの抽象化

**複数手法を比較できることが要件**である。バックエンドをプラガブルにする。

```python
class MeasureBackend(Protocol):
    name: str
    def query(self, name: str, rtype: str,
              resolver: str) -> RawResponse: ...
```

| バックエンド | 用途 | 備考 |
|---|---|---|
| `zdns` | **主力** | Apache-2.0、JSON Lines で status/answers/TTL/authorities/protocol を生保存。MX/TXT/SPF/DMARC 専用モジュールあり |
| `dnsx` | 補助・クロスチェック | MIT、`-raw` で生レスポンス |
| `dnspython` | 検証・少量 | 例外分岐が細かく制御しやすい |
| `checkdmarc` | SPF include の意味論解析 | Tree Walk 実装済み |
| `securitytrails` | **過去データのみ** | 継続計測には使わない |

`--method` で切り替え、`--compare zdns,dnspython` で同一対象を複数手法で引いて差分を出せるようにする。

### リゾルバ戦略

```yaml
# configs/measure.yaml
resolver:
  mode: local_unbound          # localhost に unbound を立てて上流にフォワード
  upstream:
    - 8.8.8.8                  # Google 公式既定 1,500 QPS/IP
    - 1.1.1.1                  # Cloudflare（公式値非公表、10 req/s で絞られる報告あり）
    - 9.9.9.9
  cross_check:
    enabled: true
    sample_rate: 0.05          # 5%を3系統で引いて差分検出

rate:
  threads: 80                  # 実効 QPS ≦ 80〜100 を目標
  timeout_sec: 5
  retries: 3
  backoff: [1, 3, 9]
  shuffle_domains: true        # 同一権威への連続クエリを避ける

cache:
  # SPF include 先は大手プロバイダに集中する
  # キャッシュなしでは同一権威に不当な負荷をかける（倫理的義務）
  spf_include: true
  mx_host_resolution: true

dkim:
  # 決定: 当面 L1/L2 まで。L3（Tatang 3,498語）は GPL-3.0 の
  # コピーレフト波及の法務確認が済むまで無効化する。
  # コンソールには「L3 開発予定」と表示すること。
  layers: [l1_core, l2_provider]
  l3_on_miss: false                   # 法務確認後に true へ
  l3_status: planned                  # UI 表示用
  negative_control: true              # ワイルドカードDNS検出
  wildcard_selector_probe: true       # *._domainkey の失効鍵を検出

extras:
  mta_sts: true
  tls_rpt: true
  bimi: true
  dnssec: true
  dane: true                          # 取得はするが日米では指標化しない
  https_fetch: false                  # 第2段で有効化（MTA-STSポリシー、BIMI SVG/VMC）
```

### エラー分類とリトライのステートマシン

```
START → QUERY(resolver_i, udp)
  ├─ NOERROR(answers>0)        → SUCCESS(observed=true,  record_present=true)
  ├─ NOERROR(answers=0/NODATA) → SUCCESS(observed=true,  record_present=false)  リトライ不要
  ├─ NXDOMAIN                  → SUCCESS(observed=true,  record_present=false, nxdomain=true)
  ├─ TC=1 (truncated)          → QUERY(resolver_i, tcp)
  ├─ SERVFAIL/REFUSED/TIMEOUT  → RETRY: attempt<max なら backoff 後 同 resolver
  │                                      attempt==max なら resolver_{i+1}
  └─ 全 resolver 枯渇           → FAIL(observed=false, record_present=null)
```

**NXDOMAIN と NODATA はリトライしない。** これらは「名前が無い」「そのタイプのレコードが無い」という確定結果であり、一時的失敗ではない。DKIMセレクタ探索では NODATA が正常系の大半を占める。

### 出力スキーマ（bronze / JSON Lines）

```json
{
  "schema_version": "1.0",
  "run_id": "2026-08",
  "ts": "2026-08-01T02:14:22Z",
  "domain": "example.co.jp",
  "query_name": "_dmarc.example.co.jp",
  "query_type": "TXT",
  "purpose": "dmarc",
  "method": "zdns",
  "resolver": "127.0.0.1->8.8.8.8",
  "protocol": "udp",
  "rcode": "NOERROR",
  "observed": true,
  "record_present": true,
  "answers": [
    {"type": "TXT", "ttl": 3600,
     "data": "v=DMARC1; p=reject; rua=mailto:xxx@dmarc25.jp"}
  ],
  "authorities": [],
  "additionals": [],
  "authoritative_ns": "ns1.example.co.jp",
  "dnssec": {"do": true, "ad": false, "rrsig_present": false},
  "retries": 0,
  "duration_ms": 42,
  "tool": "zdns",
  "tool_version": "v1.1.0"
}
```

分割された TXT レコード（255バイト超）は、**連結せずに character-string の配列のまま bronze に保存する**。連結は P5 の責務。生データの忠実性を優先する。

### メトリクス
```
input:   計測対象ドメイン数
success: observed=true のクエリ数
failed:  observed=false のクエリ数
breakdown:
  by_rcode:        {NOERROR: N, NXDOMAIN: N, SERVFAIL: N, ...}
  by_purpose:      {mx: N, spf: N, dmarc: N, dkim: N, ...}
  tcp_fallback:    N
  wildcard_detected: N     # 対照クエリが応答したドメイン数
  cross_check_diff:  N     # 複数リゾルバで結果が食い違った件数
  cache_hit_rate:    0.87
```

### 受け入れ基準
- 失敗率（`observed=false`）が 2% 未満
- ワイルドカードDNS検出が機能していること（対照クエリの実装確認）
- 同一ドメインへの連続クエリが発生していないこと（シャッフルの確認）
- 実行時間が6時間以内（GitHub Actions のジョブ上限）

### 実装メモ

**GitHub Actions ランナーは既定で UDP/TCP 53 が開いている。** ただし IP が動的なため、逆引き PTR や RIPE 連絡先の一貫性が必要なら self-hosted か固定 egress が要る。

**倫理的な作法として次を実装する。**
- SPF include 先と MX ホストの解決結果をキャッシュ（必須。大手プロバイダの権威DNSへの負荷回避）
- ドメイン順のシャッフル
- 計測の説明ページと連絡先の公開（OpenINTEL の Problems ページが手本）

**DKIM の三層辞書。**
- L1（40〜60）: Wang et al. の頻出上位40相当 + 主要ESP既定
- L2: MX/SPF から推定した事業者の既知セレクタを動的付与（MX が `*.outlook.com` なら `selector1`/`selector2` を最優先）
- L3（3,498語、Tatang 辞書）: L1/L2 で未検出だったドメインにのみ適用。**GPL-3.0 のためコピーレフト波及の法務確認が済むまでは無効化しておく**

**飽和曲線を初回に描く。** 初回フル計測時に「セレクタ数 対 新規発見ドメイン数」をプロットし、自母集団での最適セレクタ数を実証的に決める。Wang et al. は Alexa 上位1万での実験から40を採用した。

---

## P5 ── パース

### 目的
bronze の生レスポンスを構造化し、仕様に照らして解釈する。**このフェーズは何度でも作り直せる**。パーサにバグが見つかったら、bronze から再実行する。

### 入力
`p4_measure/bronze/**/*.jsonl.zst`

### 処理の要点

#### 分割TXTの連結
1つのTXTレコードは複数の character-string（各最大255オクテット、RFC 1035 §3.3）に分割されうる。**順序どおりに連結して単一文字列としてパースする**。RFC 9989 §4.7 も MUST でこれを規定している。2048bit RSA の DKIM 公開鍵も分割されるため同様。

#### SPF のエッジケース

```python
# 1. 複数の v=spf1 → PermError (RFC 7208 §4.5)
#    バージョンセクションが正確に "v=spf1" で始まるものだけを残す
#    ("v=spf10" は不一致。バージョンセクションは SP または終端で終わる)
#    0件 → none / 2件以上 → permerror

# 2. 10ルックアップ制限 (RFC 7208 §4.6.4)
#    カウント対象: include, a, mx, ptr, exists, redirect
#    非対象:       all, ip4, ip6
#    「ルックアップを行うメカニズムの数」であり、生成されるクエリ総数ではない
#    予算は評価パス全体で共有（include 内で別途10もらえるわけではない）

# 3. void lookup 2回制限（SHOULD だが主要受信者は実質強制）
#    NXDOMAIN または NODATA を返すクエリが void lookup

# 4. all と redirect の優先順位
#    all がレコードに存在する場合、redirect は無視される
#    "... -all redirect=..." では -all が優先され redirect は死ぬ

# 5. Valimail 動的SPF の検出
#    include:%{i}._ip.%{h}._ehlo.%{d}._spf.vali.email 形式を検出したら
#    「動的SPF・静的にルックアップ数を数えても無意味」と分類する

# 6. フラット化の検出
#    ip4/ip6 が数十〜数百 かつ include がほぼ無い → フラット化疑い
```

#### DMARC の二重計算

**受信側は依然として RFC 7489 のまま**である（RFC 9990 形式でレポートを送っている大手は United Internet のみ、全レポーターの0.6%）。したがって RFC 7489 準拠の判定が「実際に効いている強度」に最も近い。しかし Tree Walk 差分は将来必ず顕在化するため、両方を計算して保持する。

```python
def classify_policy(record):
    p   = record.p
    pct = record.pct if record.pct is not None else 100
    t   = record.t   if record.t   is not None else 'n'
    has_rua = bool(record.rua)

    # RFC 7489 実効強度（現在の受信側挙動に最も近い）
    if p == 'reject':
        eff_7489 = 'reject' if pct >= 100 else ('quarantine' if pct > 0 else 'none')
    elif p == 'quarantine':
        eff_7489 = 'quarantine' if pct >= 100 else ('none' if pct == 0 else 'quarantine_partial')
    else:
        eff_7489 = 'none'

    # RFC 9989 実効強度（pct無視、t=y で1段downgrade）
    eff_9989 = p
    if t == 'y':
        eff_9989 = downgrade_one_step(p)   # reject→quarantine, quarantine→none

    return eff_7489, eff_9989, build_label(p, pct, t, has_rua), has_rua
```

#### ポリシー強度の分類ラベル

| 名目 | 修飾子 | RFC 7489実効 | RFC 9989実効 | ラベル |
|---|---|---|---|---|
| `p=reject` | なし, rua有 | reject | reject | `enforced_reject` |
| `p=reject` | `pct=10` | ほぼnone | reject | `nominal_reject_weak_pct` |
| `p=reject` | `t=y` | reject | quarantine相当 | `nominal_reject_testing` |
| `p=reject` | rua未設定 | reject（可視性ゼロ） | reject | `blind_reject` |
| `p=quarantine` | `pct=50` | ~50%quarantine | quarantine | `nominal_quarantine_weak_pct` |
| `p=none` | rua有 | 監視のみ | 監視のみ | `monitoring` |
| `p=none` | rua無 | 実質無効 | 実質無効 | `ineffective` |

#### Organizational Domain の二重解決

PSL 解決と Tree Walk 解決の両方を計算し、`org_domain_divergence` フラグを保存する。日本の `.co.jp` 系や多階層サブドメインで差分が出る候補を事前に洗い出すため。

Tree Walk（RFC 9989）の手順は、Author Domain から `_dmarc` ラベルを付けて上位へ辿り、`psd=y` または `psd=n` を含む有効なレコードを見つけたら停止。DoS 対策で1ドメインあたり最大8クエリ。8ラベル超のドメインは Author Domain を最初に照会し、続行時は最右7ラベルから再開。

#### DMARC タグの扱い

- 未知タグは MUST スキップ（RFC 9989 §4.7）だが、**生値は round-trip 保存のため必ず保持**する
- タグ名は case-insensitive、`v=DMARC1` の値のみ case-sensitive で厳密一致
- 重複タグは警告を出し、最初の出現値を採用しつつ `has_duplicate_tag` フラグを立てる
- `_dmarc` に2件以上の `v=DMARC1` があればポリシー全体が無効

#### External Destination Verification

`rua`/`ruf` の宛先ドメインがポリシードメインと異なる場合、`<reporting-domain>._report._dmarc.<external-domain>` に `v=DMARC1` があるか検証する。無ければ「外部宛先未認可、レポート届かない」として記録。

さらに **rua/ruf 宛先ドメインの登録状況**（WHOIS/RDAP または NS 有無）を検査する。Hureau et al.（PAM 2024）は9,121件の DMARC レコード内メールアドレスが未登録ドメインを指しており、第三者が登録すればレポートが漏洩しうると指摘している。

### 出力スキーマ（silver / facts.parquet）

```
fact_id              STRING  PK
domain_id            STRING  FK
entity_id            STRING  FK
run_id               STRING
measured_month       DATE

-- 観測の可否（原則5）
observed             BOOLEAN
record_present       BOOLEAN  NULL可

-- 生値（原則1の延長。silver でも生を捨てない）
raw_spf              STRING
raw_dmarc            STRING
raw_mx               JSON

-- SPF
spf_present          BOOLEAN
spf_valid            BOOLEAN
spf_error            STRING   permerror | temperror | multiple_records | null
spf_all_qualifier    STRING   - | ~ | ? | +
spf_lookup_count     INTEGER
spf_void_count       INTEGER
spf_exceeds_limit    BOOLEAN
spf_includes         ARRAY<STRING>
spf_is_flattened     BOOLEAN
spf_is_dynamic       BOOLEAN  Valimail マクロ等

-- DMARC
dmarc_present        BOOLEAN
dmarc_p              STRING
dmarc_sp             STRING
dmarc_np             STRING
dmarc_pct            INTEGER  RFC 7489 時代のレコードのみ
dmarc_t              STRING
dmarc_psd            STRING
dmarc_adkim          STRING
dmarc_aspf           STRING
dmarc_rua            ARRAY<STRING>
dmarc_ruf            ARRAY<STRING>
dmarc_has_duplicate_tag  BOOLEAN
dmarc_multiple_records   BOOLEAN

effective_7489       STRING   none | quarantine | reject
effective_9989       STRING
policy_label         STRING   enforced_reject 等
blind_enforcement    BOOLEAN  enforcement だが rua 無し

org_domain_psl       STRING
org_domain_treewalk  STRING
org_domain_divergence BOOLEAN

rua_external         BOOLEAN
rua_authorized       BOOLEAN  _report._dmarc の確認結果
rua_domain_unregistered BOOLEAN

-- DKIM（三値表現。原則5の適用）
dkim_status          STRING   detected | not_found_in_known_selectors | not_applicable
dkim_selectors       ARRAY<STRING>
dkim_key_bits        ARRAY<INTEGER>
dkim_testing_flag    BOOLEAN  t=y
dkim_revoked         BOOLEAN  空の p=
dkim_wildcard_suspect BOOLEAN 対照クエリが応答した

-- MX
mx_present           BOOLEAN
mx_hosts             ARRAY<STRING>

-- 周辺プロトコル
mta_sts_present      BOOLEAN
mta_sts_id           STRING
mta_sts_mode         STRING   HTTPS取得時のみ
tls_rpt_present      BOOLEAN
tls_rpt_rua          ARRAY<STRING>
bimi_present         BOOLEAN
bimi_has_vmc         BOOLEAN
dnssec_signed        BOOLEAN
dane_present         BOOLEAN

-- 仕様バージョン
spec_version         STRING   rfc7489 | rfc9989
parser_version       STRING
```

### メトリクス
```
input:   bronze のレコード数
success: パースできた件数
failed:  パース例外
breakdown:
  spf_permerror:          N
  spf_exceeds_10_lookup:  N
  dmarc_multiple_records: N
  dmarc_duplicate_tag:    N
  org_domain_divergence:  N   # PSL と Tree Walk で判定が割れた件数
  dkim_wildcard_suspect:  N
  rua_unauthorized:       N
  rua_domain_unregistered: N
```

### 受け入れ基準
- パース失敗が 0.5% 未満
- `checkdmarc` の出力とサンプル100件で突合し、SPF/DMARC の判定が一致すること
- 分割TXTの連結が正しく動作すること（255バイト超のレコードでテスト）
- `spec_version` が全レコードに付与されていること

### 実装メモ
- 参照実装は **checkdmarc**（Tree Walk 実装済み、5.17.3系）。ただし丸ごと使うのではなく、判定ロジックを自前で持ち、checkdmarc は検証用の対照として使う
- DMARC レポートの取り込みが将来必要になったら **parsedmarc**（RFC 9990 新スキーマと旧 RFC 7489 スキーマの両方をパース、10.4.x系）

---

## P6 ── 推察

### 目的
fact からメール基盤とセキュリティ製品を推定する。**必ず confidence と evidence を伴う**。

### 入力
`p5_parse/facts.parquet` + `configs/fingerprints/*.yaml`

### フィンガープリント定義の形式

```yaml
# configs/fingerprints/security_gw.yaml
version: 2026-08-01
rules:
  - id: pp-mx-01
    vendor: Proofpoint
    product: Proofpoint Enterprise Protection
    category: security_gateway
    match:
      record: MX
      pattern: '^mx0[ab]-[0-9a-f]{8}\.pphosted\.com\.?$'
    confidence: high
    source: dmarc.mx

  - id: pp-mx-02
    vendor: Proofpoint
    product: Proofpoint Essentials
    category: security_gateway
    match:
      record: MX
      pattern: '\.ppe-hosted\.com\.?$'
    confidence: high

  - id: mfilter-spf-01
    vendor: デジタルアーツ
    product: m-FILTER@Cloud
    category: security_gateway
    match:
      record: SPF_INCLUDE
      pattern: '^(_spf1\.)?spf\.mail\.system\.digitalartscloud\.com$'
    confidence: high
    source: daj.jp/bs/serverlist/
    region: JP

  - id: guardianwall-spf-01
    vendor: キヤノンITソリューションズ
    product: GUARDIANWALL メールセキュリティクラウド
    category: security_gateway
    match:
      record: SPF_INCLUDE
      pattern: '^_spf\.guardianwall\.jp$'
    confidence: high
    region: JP
```

### 二段推定

ゲートウェイ型製品は MX を自社に向けさせるため、MX だけでは背後の実基盤が見えない。SPF include / DKIM CNAME / 所有権確認TXT の残存から背後を推定する。

```python
INFERENCE_PRIORITY = {
    "DKIM_CNAME":       4,   # 署名基盤。最も実基盤に近い
    "MX":               3,   # インバウンド経路
    "SPF_INCLUDE":      2,   # アウトバウンド送信元
    "VERIFICATION_TXT": 1,   # 最も弱い
}

def infer(fact, rules):
    """
    MX が security_gateway にマッチした場合でも
    SPF_INCLUDE の mail_platform ルールを別カテゴリとして残す。
    単一ベンダーに丸めない（原則2）。
    """
```

典型的な二段パターン。

| 観測 | 推定 | 確度 |
|---|---|---|
| MX=Mimecast + SPF `spf.protection.outlook.com` | 実基盤 M365 | 中（DKIM CNAME が `*.onmicrosoft.com` なら高） |
| MX=Cisco `iphmx.com` + TXT `MS=ms...` + DKIM `→onmicrosoft.com` | 実基盤 M365 | 高（3点整合） |
| MX=IIJ `*.securemx.jp` + SPF M365 include | IIJセキュアMXが前段、背後は M365 | 高 |
| MX=HENNGE `mo.*.hdemail.jp` + SPF M365 include | HENNGE One + M365 | 高 |
| MX=GUARDIANWALL + SPF `_spf.guardianwall.jp` + M365 include | M365 + GUARDIANWALLクラウド | 高 |

### 所有権確認TXT の「過去の痕跡」判定

`MS=` や `google-site-verification=` は削除されずに残りやすく、単独では「利用中」と断定できない。

```python
def is_stale_verification(fact, prefix):
    """
    所有権確認TXTがあっても、対応するMX/SPF/DKIMの裏付けが無ければ
    「過去に検討/併用したが現行のメール基盤ではない」と判定する。
    月次差分での観測が最も確実な判別手段。
    """
    if prefix == "MS=":
        return not (fact.mx_matches_m365 or
                    "spf.protection.outlook.com" in fact.spf_includes or
                    fact.dkim_cname_onmicrosoft)
```

3か月連続で裏付けが出なければ `stale` に降格する。

### パークドメイン分類

**本システム固有の差別化指標。**網羅展開した30,000ドメインのうち大半は送信に使われていない。その中で「適切に固められたもの」と「単に放置されたもの」を区別する。

攻撃者から見れば、送信実績がなく監視もされていないドメインはなりすましの理想的な出発点になる。したがって非送信ドメインをどう扱っているかは、その組織のメールセキュリティ成熟度を測る良い代理指標になる。

#### 標準的なパークドメインの固め方

M3AAWG「Protecting Parked Domains Best Common Practices」が推奨する構成。

```dns
example.com.               IN MX    0 .                    ; RFC 7505 Null MX
example.com.               IN TXT   "v=spf1 -all"
*._domainkey.example.com.  IN TXT   "v=DKIM1; p="          ; 失効鍵
_dmarc.example.com.        IN TXT   "v=DMARC1; p=reject; sp=reject; adkim=s; aspf=s; rua=mailto:..."
```

`p=` を空にすると失効を意味する。RFC 6376 §3.6.1 が "An empty value means that this public key has been revoked" と規定しており、ワイルドカードにしておけば攻撃者がどのセレクタを騙っても失効鍵に当たる。

ただし **DKIM ワイルドカードの実効的な防御力は小さい**。そもそもレコードが存在しなければ DKIM 検証は失敗するため、「無い」と「失効している」で結果は変わらない。意味があるのは不作為ではなく明示的な宣言であること、および監査で意図を示せることである。実際に効いているのは `p=reject` と strict alignment（`adkim=s; aspf=s`）の方であり、SPF の `-all` だけでは転送やアライメント緩和の隙間が残る。

DMARCbis の `np=`（存在しないサブドメイン用ポリシー）を組織ドメインに一本置けばサブドメイン全体をカバーできるが、**受信側で実装した大手はまだ確認できていない**（DR-14）。当面は `sp=reject` が実質的な防御線であり、`np=` は将来への布石という位置づけになる。DNSSEC 署名ドメインで `np=` が期待通り動かない相互運用バグ（RFC 9824 との衝突）も未解決である。

#### 分類ロジック

| 分類 | MX | SPF | DMARC | 意味 |
|---|---|---|---|---|
| `hardened_parked` | Null MX（`0 .`） | `-all` | `p=reject` | **模範的。**明示的に送信も受信も否定 |
| `defended_parked` | 無し または Null MX | `-all` または `~all` | `p=reject` or `quarantine` | 十分に固められている |
| `intentional_no_send` | 無し | `-all` または `~all` | 何でも / 無し | 送信禁止の意図はあるが DMARC が弱い |
| `neglected` | 無し | 無し | 無し | **放置。**なりすましの出発点になりうる |
| `active_sending` | 有り | 有り | 有り | 通常の送信ドメイン。パーク分類の対象外 |
| `inconsistent` | 有り | 無し 等 | | 矛盾。要個別確認 |

```python
def classify_park(fact):
    if fact.mx_present and not fact.null_mx:
        return "active_sending" if fact.spf_present else "inconsistent"

    hard_deny = fact.spf_all_qualifier == "-"
    soft_deny = fact.spf_all_qualifier in ("-", "~")
    enforced  = fact.effective_7489 in ("reject", "quarantine")

    if fact.null_mx and hard_deny and fact.effective_7489 == "reject":
        return "hardened_parked"
    if soft_deny and enforced:
        return "defended_parked"
    if soft_deny:
        return "intentional_no_send"
    if not fact.spf_present and not fact.dmarc_present:
        return "neglected"
    return "inconsistent"
```

#### この分類が生む固有の数字

Czybik et al.（IMC 2023）は「MX のないドメインの10.4%が SPF を持ち、うち53.1%が `-all`/`~all`」と報告している。これはまさに `intentional_no_send` を捉えたものだが、同論文は Null MX と DMARC を組み合わせた段階化まではしていない。

本システムは Null MX の有無と DMARC ポリシーを加えることで、**防御の意図の強さを4段階で示せる**。TwoFive の「ドメインベース18.5%」という単一の数字に対し、次の二つを分けて提示できる。

- **送信ドメインの強制率**: `active_sending` のうち enforcement に達している割合
- **非送信ドメインの防御率**: パークドメインのうち `hardened_parked` + `defended_parked` の割合

分母の質を開示できるのは、企業とドメインの対応を透明にする設計の副産物である。

### API連携型製品という構造的盲点

次の製品は MX を変更せず API / OAuth 連携で動作するため、**原理的に DNS へ痕跡を残さない**。「検出されなかった＝使っていない」ではないことを、出力とレポートの両方で明示する。

Abnormal Security / Avanan（Check Point Harmony Email）/ Darktrace EMAIL / Vade for M365 / Perception Point / Agari

### 出力スキーマ（silver / inferences.parquet）

```
inference_id     STRING  PK
domain_id        STRING  FK
entity_id        STRING  FK
run_id           STRING
measured_month   DATE

category         STRING  mail_platform | security_gateway | esp | dmarc_vendor
vendor           STRING
product          STRING
confidence       STRING  high | medium | low
is_stale         BOOLEAN 過去の痕跡と判定

evidence         JSON    [{record_type, matched_value, rule_id}, ...]
rule_ids         ARRAY<STRING>
fingerprint_version STRING
note             STRING

-- パークドメイン分類（inference とは別テーブルでも可）
park_class       STRING  hardened_parked | defended_parked | intentional_no_send
                         | neglected | active_sending | inconsistent
park_has_null_mx BOOLEAN
park_has_wildcard_dkim_revoked BOOLEAN

-- 検出不能の明示
undetectable_reason STRING  api_mode_product | reseller_opaque | null
```

### メトリクス
```
input:  fact の件数
output: inference の件数
breakdown:
  by_category:            {mail_platform: N, security_gateway: N, ...}
  by_confidence:          {high: N, medium: N, low: N}
  domains_with_no_inference: N        # 未知パターン。要調査
  unknown_mx_hosts:       [上位N件]    # 辞書拡充の候補
  stale_verification:     N
```

### 受け入れ基準
- `domains_with_no_inference` が 20% 未満
- `unknown_mx_hosts` の上位20件が manifest に出力されること（**これが国内ベンダー辞書を育てる主要な経路になる**）
- 同一ドメインに複数の inference が付くことを許容していること

### 実装メモ

**初期シードとして `covert-labs/mx-intel` を使う。** Python dict 形式で MX ドメイン→ベンダーを約80件、ASN→ベンダーのマッピングを持つ。約830万ドメインへの MX 一括名前解決に基づく。日本勢は `securemx.jp`（IIJ）、`activegate-ss.jp`（クオリティア）など一部のみ収録なので、そこに本システムの日本勢辞書を追加する。

**未知 MX ホストの頻度順リストが、辞書を育てる装置になる。** リサーチでは NRIセキュア、ラック、富士通、NEC、ソフトバンク、大塚商会など多数の国内ベンダーの固定ホスト名を特定できなかった。これらは実測データからの帰納的発見でしか埋まらない。運用コンソールに「未知ホスト名を頻度順に表示し、手動でベンダーを紐付けて辞書に追記する」画面を必ず作ること。

**日本市場特有の注意点。**
- さくらインターネットは専用 include ではなく `a:wwwNNNN.sakura.ne.jp mx` 形式。include 検索だけでは取りこぼす
- 番号付き include（`spf[N].gmoserver.jp`、`spf[NN].biglobe.ne.jp`）は番号部分をワイルドカード化する
- IIJ セキュアMX の DKIM 署名ドメインは `dxg.dox.jp` でアライメント不可。ゲートウェイ独自の署名ドメインを実基盤と誤認しないよう除外リストを持つ
- 大塚商会「たよれーる」等のディストリビュータ経由では DNS 上は M365 のみが現れ、リセラーは判別不能

---

## P7 ── 集計

### 目的
個社データから公開用の統計を生成し、前月との差分を計算する。

### 入力
`p5_parse/facts.parquet` + `p6_infer/inferences.parquet` + 前月の gold

### 処理

1. 全社統計（母集団別）
2. 業種別集計（共通12分類）。**1業種あたり最小 n=5 のセル秘匿を適用**し、下回る業種は「その他」に統合
3. 前月差分（新規導入、ポリシー強化、後退、消滅）
4. 個社明細（第2層用）

### セル秘匿

公的統計の実務基準では、日本の総務省統計局が n=1 または 2 を1次秘匿、米欧の多くの機関は3または5未満を秘匿する（FCSM Statistical Policy Working Paper 22、NIST SP 800-188）。k-匿名性の推奨 k は一般に3〜5、実務では k=5 が広く採用。

本システムは保守的に **n=5** を採る。東証プライム約1,552社を12分類に割ると1分類平均約130社なので、12分類軸なら閾値は容易に満たす。逆に33業種のまま公開すると空運業・海運業等が n<5 に近づくため、細分軸での公開は避ける。

### 出力スキーマ

```
-- gold/stats_overall.parquet
measured_month       DATE
population_id        STRING
total_entities       INTEGER
total_domains        INTEGER

-- 企業数ベースとドメインベースの両方を必ず出す
spf_adopted_entities     INTEGER
spf_adopted_domains      INTEGER
dmarc_adopted_entities   INTEGER
dmarc_adopted_domains    INTEGER
dmarc_enforced_entities  INTEGER   -- quarantine or reject
dmarc_enforced_domains   INTEGER

-- 名目と実効を分ける
nominal_reject_domains   INTEGER
enforced_reject_domains  INTEGER   -- pct無し・t=n・rua有
blind_reject_domains     INTEGER   -- rua無し

dkim_detected_domains        INTEGER
dkim_not_found_domains       INTEGER   -- 「未設定」ではない
mta_sts_domains              INTEGER
tls_rpt_domains              INTEGER
bimi_domains                 INTEGER
dnssec_domains               INTEGER

maturity_stage_dist  JSON   -- Stage 0〜4 の分布

-- パークドメイン指標（本システム固有）
sending_domains          INTEGER   -- active_sending
sending_enforced         INTEGER   -- うち enforcement 到達
parked_domains           INTEGER   -- 非送信ドメイン合計
parked_hardened          INTEGER   -- hardened_parked
parked_defended          INTEGER   -- defended_parked
parked_intentional       INTEGER   -- intentional_no_send
parked_neglected         INTEGER   -- neglected
park_defense_rate        DOUBLE    -- (hardened + defended) / parked

-- 地域別クロス集計の軸
dane_domains             INTEGER
dane_dnssec_valid        INTEGER   -- TLSA があり親ゾーンも署名済み
dane_orphan              INTEGER   -- TLSA はあるが DNSSEC 未署名で実効しない

delta_prev_month     JSON
spec_version         STRING
```

```
-- gold/stats_by_sector.parquet
measured_month     DATE
population_id      STRING
common12_code      STRING
common12_label     STRING
n_entities         INTEGER
suppressed         BOOLEAN   -- n<5 で秘匿した
（以下 stats_overall と同じ指標）
```

### 成熟度ステージ

MTA-STS / BIMI / DANE はいずれも DMARC を事実上の前提とするため、単純加算は下位項目を二重評価する。順序性を反映した階層ステージで表現する。

```
Stage 0: SPFのみ / レコードなし
Stage 1: SPF + DKIM(既知セレクタで検出) + DMARC p=none
Stage 2: DMARC p=quarantine/reject（強制ポリシー）
Stage 3: Stage 2 + (MTA-STS enforce または TLS-RPT) + DNSSEC署名
Stage 4: Stage 3 + BIMI(有効SVG+VMC) または DANE/TLSA
```

DANE は日米ではほぼ常時ゼロ（日本の DANE MX ホストは約36〜57、米国は Tranco 5.5M 中30ドメイン）なので、スコアの主軸には入れず観測項目に留める。

### メトリクス
```
breakdown:
  suppressed_sectors:  N      # セル秘匿した業種数
  entities_new:        N
  entities_removed:    N
  policy_upgraded:     N      # none→quarantine→reject
  policy_downgraded:   N
  domains_disappeared: N      # 2連続観測での不在のみカウント
```

### 受け入れ基準
- 秘匿後の合計値から個社が逆算できないこと（2次秘匿の確認）
- 企業数ベースとドメインベースの両方が出力されていること
- 前月差分が「消えた」と「取れなかった」を混同していないこと

---

## P8 ── 公開

### 目的
静的サイトを生成し Cloudflare Pages にデプロイする。

### 二層構成

| 層 | 内容 | アクセス制御 | 条件 |
|---|---|---|---|
| **第1層** | 全社統計・業種別集計 | 無条件公開 | ライセンス上公開可のデータのみ |
| **第2層** | 個社名付き明細 | Cloudflare Access（50人まで無料） | 事前通知後、最低30日（推奨60日）の訂正期間を経てから |

フィルタは `is_individual_level` と `license` カラムで機械的に適用する。

### 表現上の規約

これは法務要件でもあるため、実装時に妥協しないこと。

- **配色**: 合格＝緑、要改善＝橙、未対応＝グレー寄りの赤の3段階。赤の面積を最小化する
- **語彙**: 「危険」「脆弱」等の断定を避ける。「業界標準に照らして REQUIRED を満たさない」「p=none のため spoofing 抑止効果は限定的」のような標準準拠の事実記述に限定
- **順位付けをしない**: 総合順位・A〜Fグレードは付けない。代わりに「達成した標準のチェックリスト」を提示
- **事実と推察の視覚的分離**: 同じカードの中で「観測値」と「推定」を明確に区切る
- **限界の明示**: 「本サイトは標準準拠の計測であり、総合的セキュリティ評価ではない」を常時表示

### 必須ページ

- トップ（総括指標＋時系列＋業種別）
- 方法論（Methodology）
- 免責事項・利用規約
- 訂正申告窓口
- 変更履歴（changelog）
- データダウンロード（Parquet / CSV / JSON、ライセンス表記付き）

### 技術構成

- Observable Framework（静的生成、data loader がビルド時に Parquet を生成）
- Observable Plot（時系列・積み上げ棒・small multiples）
- DuckDB-WASM または hyparquet でブラウザから Parquet を直読
- 高密度テーブルは `Inputs.table`

2,000〜3,000行 × 数十列 × 数十カ月は数MB程度なので、クライアントサイド分析で十分快適に動く。

---

## 7. 運用コンソール

**開発初期の主戦場**。ここが良くできていれば、ロジックの改善サイクルが速く回る。

### 7.1 技術構成

```
console/
├── backend/           # FastAPI
│   ├── main.py
│   ├── runs.py        # run の一覧・詳細・manifest 読み込み
│   ├── execute.py     # フェーズの起動（subprocess）
│   ├── inspect.py     # Parquet / JSONL を DuckDB で読んで返す
│   └── dict_edit.py   # フィンガープリント辞書の編集
└── frontend/          # Vite + React + TypeScript
```

ローカル専用。認証なし。`uvicorn` で起動し `localhost:8000` で開く。

### 7.2 画面要件

#### 画面1 ── パイプライン全景
八工程を横一列に並べ、各工程のカードに次を表示する。

```
┌─────────────────────────────────────────────────┐
│ P4 DNS計測                          ● success   │
│ 2026-08-01 02:00 → 02:41 (41分33秒)            │
│                                                 │
│ 入力 2,847  成功 2,801  失敗 31  スキップ 15    │
│ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░  98.4%       │
│                                                 │
│ 失敗内訳                                        │
│   TIMEOUT   14  ▓▓▓▓▓▓▓                        │
│   SERVFAIL  12  ▓▓▓▓▓▓                         │
│   REFUSED    3  ▓▓                              │
│                                                 │
│ ⚠ WILDCARD_DNS 7件                             │
│                                                 │
│ [再実行] [詳細を見る] [出力を検査]              │
└─────────────────────────────────────────────────┘
```

工程間の矢印には、その工程で件数がどう変化したかを表示する（2,847 → 2,801 のように）。**どこで何件落ちたかが一目で分かる**ことが最重要要件。

#### 画面2 ── フェーズ実行
- 母集団プリセットを選択（jp-prime / jp-standard / jp-growth / jp-nikkei225 / us-fortune500 / us-sp500）
- 実行するフェーズを選択（単独 / P1から連続 / 特定フェーズ以降）
- ドライラン、件数制限（`--limit 100` で開発中の高速反復）
- 実行ログのストリーミング表示

#### 画面3 ── レコード検査
- 任意のフェーズの出力を DuckDB でクエリして表示
- ドメイン名で検索し、そのドメインについて全フェーズの記録を縦に並べる
  - bronze の生 JSON → silver の fact → inference → gold への寄与
- **これが「なぜこの判定になったか」を追跡する主要な手段**になる

#### 画面4 ── 手法比較
- 同一ドメイン集合を複数バックエンド（zdns / dnspython / securitytrails）で引いた結果を横並び
- 差分のあるレコードだけをフィルタ
- リゾルバ間（8.8.8.8 / 1.1.1.1 / 9.9.9.9）の差分も同様に

#### 画面5 ── 辞書メンテナンス
- **未知 MX ホスト名を頻度順に表示**し、ベンダーを手入力して `configs/fingerprints/*.yaml` に追記
- 未知 SPF include も同様
- 追記後、その場で P6 を再実行して効果を確認
- DKIM 辞書の状態を表示する。L1・L2 は稼働中、**L3（Tatang 3,498語）は「開発予定」バッジ**を出し、GPL-3.0 の法務確認待ちである旨をツールチップで示す
- 飽和曲線（セレクタ数 対 新規発見ドメイン数）を表示し、L1/L2 で十分かを判断できるようにする
- **この画面が国内ベンダー辞書を育てる装置**である。リサーチで埋まらなかった空白は、ここでしか埋まらない

#### 画面6 ── 月次差分
- 前月と当月で状態が変わったドメインの一覧
- 変化の種類でフィルタ（ポリシー強化 / 後退 / 新規 / 消滅 / 基盤変更）

### 7.3 コンソールの受け入れ基準
- 全フェーズが画面から実行でき、ログがリアルタイムで見えること
- 任意のドメインについて、bronze から gold までの経路を追跡できること
- 未知 MX ホストから辞書追記までが3クリック以内で完結すること

---

## 8. 開発ロードマップ

「アドホック実行で工程ごとに検証し、ロジックが固まってから自動化する」という方針に沿って段階を切る。

### Sprint 1 ── 骨格とP1（日本）
- リポジトリ構成、`contracts.py`、`manifest.py`
- P1（jp-prime のみ。米国側は Sprint 1.5）
- コンソールの画面1と画面2（最小版）
- **完了条件**: 東証プライム約1,550社の entities.parquet が生成され、コンソールで件数が見える

### Sprint 1.5 ── P1（米国・グローバル）
- us-fortune500 と global500 を追加
- GLEIF を主キーとする identity 構築、LEI 欠損時のフォールバック
- 日本企業の重複排除（jp-prime と global500 の突合）
- 共通12分類への写像（SIC 経由と手動マッピング）
- **完了条件**: 重複排除後の実効企業数が 2,250〜2,450 に入り、`population_ids` が正しく配列で保持される

### Sprint 2 ── P2・P3
- CT ログ、SPF redirect、DMARC rua からの候補展開
- 三段確度フラグ
- コンソールの画面3
- **完了条件**: confirmed が候補の30%以上、任意のドメインの判定根拠が追跡できる

### Sprint 3 ── P4
- zdns バックエンド、unbound、エラー分類ステートマシン
- **階層別計測**（A: フル / C: 簡易）の実装
- DKIM L1/L2 辞書、対照クエリ、ワイルドカードセレクタ探索
- 周辺プロトコル（MTA-STS / TLS-RPT / BIMI / DNSSEC / DANE）
- **完了条件**: 失敗率2%未満、30,000ドメインで実行時間3時間以内、bronze が JSON Lines で保存される

### Sprint 4 ── P5
- SPF/DMARC パーサ、二重計算、Tree Walk
- checkdmarc との突合テスト
- **完了条件**: サンプル100件で checkdmarc と判定一致

### Sprint 5 ── P6
- フィンガープリント辞書（mx-intel をシードに国内勢を追加）
- 二段推定
- **パークドメイン分類**（hardened / defended / intentional / neglected）
- コンソールの画面5（辞書メンテナンス、L3「開発予定」表示、飽和曲線）
- **完了条件**: 未推定が20%未満、未知ホスト上位20件が出る、パーク分類が全非送信ドメインに付与される

### Sprint 6 ── P7・US側
- 集計、セル秘匿、前月差分
- P1 に us-fortune500 を追加、共通12分類の写像
- コンソールの画面4と画面6
- **完了条件**: 日米が同一軸で並ぶ

### Sprint 7 ── P8 第1層
- Observable Framework、方法論・免責ページ
- **完了条件**: 第1層が Cloudflare Pages で公開できる

### Sprint 8 ── 自動化
- GitHub Actions 月次 cron
- **60日自動停止対策**（データ無変更でも必ずコミット、keepalive、外形監視）
- R2 への退避
- **完了条件**: 人手を介さず月次で回る

### Sprint 9 ── 第2層と事前通知

第2層（個社名付き）を出すことは決定済み。ただし公開の前に通知の運用が立ち上がっている必要がある。

- 通知対象の連絡先収集（`security.txt` / `security@` / `postmaster@` / IR窓口）
- 通知メールのテンプレートと送信基盤（**送信元自身の SPF/DKIM/DMARC を完全準拠させること**。通知者の認証設定が問われる）
- 毎秒1通のレート制限、オプトアウト管理
- 訂正申告窓口と訂正履歴ページ
- Cloudflare Access による第2層の保護
- **完了条件**: 事前通知を送付し、60日の訂正期間を経て第2層が公開できる

想定される到達率と是正率は次のとおり（DR-18）。過度な期待をしないこと。

| 指標 | 実測値 | 出典 |
|---|---|---|
| security.txt 普及率（Fortune 500） | 約4% | Cybernews / M. Repa, 2024年11月 |
| RFCエイリアスの到達率（Top 1M） | 24.16%（バウンスしないだけ） | Soussi/Korczyński, IEEE 2020 |
| SPF不備通知の2週間後是正率 | **3.3%** | Czybik et al., IMC 2023（111,951通送信） |
| 法的フレーミング＋郵送の場合 | 76.3% | Maass et al., USENIX 2021 |

日本市場では「不審メール扱いされるリスク」「セキュリティ窓口の不在」「法務部門の関与」という三重の障壁がある。文面には検証可能な正規ドメイン上のURLを必ず含め、営業要素を一切排除すること。広告宣伝性がなければ特定電子メール法の「特定電子メール」には当たらないと整理できる。

### Sprint 10 ── 運用の定常化

- 未知 MX ホストの手動同定を月次のルーチンに組み込む
- 訂正申告への対応 SLA（48〜72時間で審査）
- 変更履歴（changelog）の自動生成
- Zenodo への DOI 付与（学術的な引き継ぎ可能性の確保）

### 当面やらないこと

**バックフィル（SecurityTrails / DNSDB）は行わない。** ただし個人研究として非商用が明確になったため、**OpenINTEL（CC BY-NC-SA 4.0）が利用可能になった**。SPF と MX に限れば2016年（Alexa）または2019年（Umbrella）まで無料で遡れる。自前計測が12点貯まるまでの間、第三者集計（TwoFive / Proofpoint / Valimail の公表値）と併せて「文脈系列」として提示することを検討する。

OpenINTEL は `_dmarc` を計測していないため DMARC の遡及はできない。この制約は変わらない。

---

## 9. 横断的な受け入れ基準

実装完了の判定に使う。

### 機能
- [ ] 八工程がそれぞれ単独で実行でき、単独で再実行できる
- [ ] 全工程が `_manifest.json` を出力し、コンソールで可視化される
- [ ] 母集団を設定ファイルの切り替えだけで変更できる
- [ ] 同一対象を複数手法で計測して比較できる
- [ ] 任意のドメインについて bronze から gold まで追跡できる
- [ ] 未知 MX ホストから辞書追記までが画面上で完結する

### データ整合性
- [ ] silver / gold が bronze から完全に再生成できる
- [ ] `observed` と `record_present` が全レコードで区別されている
- [ ] fact と inference がスキーマレベルで分離されている
- [ ] 全 inference に confidence と evidence がある
- [ ] `spec_version` が全 fact に付与されている
- [ ] 「消滅」判定に2連続観測を要求している

### 法務・倫理
- [ ] JPX の `data_j.xls` を参照していない
- [ ] fortune.com をスクレイピングしていない
- [ ] 個人名を含むメールアドレスを収集・保存していない
- [ ] 出典表記が成果物に含まれている
- [ ] 計測の説明ページと連絡先が公開されている
- [ ] SPF include と MX ホストの解決結果をキャッシュしている
- [ ] 第2層に事前通知の運用が紐づいている

### 持続性
- [ ] 60日自動停止対策が実装されている
- [ ] bronze のバックアップが R2 以外にも存在する
- [ ] 認証情報が個人アカウントでなく組織で管理されている
- [ ] README に引き継ぎ計画が明記されている

---

## 10. Claude Code への初期指示

以下をそのまま渡せば着手できる。

```
このリポジトリで、メール認証月次計測システムを実装します。
設計仕様は DESIGN.md にあります。まず全文を読んでください。

今回のスコープは Sprint 1（日本側のみ）です。
米国・グローバル側（Sprint 1.5）は次回に回します。

前提として、本プロジェクトは個人研究として個人アカウントで運営します。
非商用が明確なため OpenINTEL 等の CC BY-NC 系データも将来利用可能ですが、
JPX の data_j.xls と fortune.com は非商用であっても使いません。

作るもの:
1. リポジトリ骨格（DESIGN.md 第4章のディレクトリ構成）
2. src/mailauth/contracts.py
   - 全フェーズの入出力スキーマを Pydantic モデルで定義
   - DESIGN.md 第6章の各スキーマに対応
3. src/mailauth/manifest.py
   - RunManifest クラス。DESIGN.md 5.2 の形式で JSON を書く
   - コンテキストマネージャとして使え、例外時も manifest を残すこと
4. src/mailauth/cli.py
   - Typer で八工程のサブコマンドを定義（P1以外は未実装スタブ）
5. src/mailauth/p1_population/
   - configs/populations/jp-prime.yaml を読んで entities.parquet を生成
   - EDINETコードリスト（EdinetcodeDlInfo.csv、cp932、13列）を API 経由で取得
   - 証券コードが空でないレコードを上場企業として抽出
   - 国税庁法人番号と gBizINFO で enrich
   - 業種は EDINET の提出者業種（11列目）を一次分類とし、
     configs/industry/edinet33_to_common12.csv で共通12分類に写像
6. console/backend + console/frontend の最小版
   - パイプライン全景（画面1）とフェーズ実行（画面2）のみ
   - FastAPI + Vite/React/TypeScript

制約:
- JPX の data_j.xls は絶対に使わないこと（商用二次利用が規約で禁止）
- EDINET はスクレイピング禁止。API 経由のみ
- SEC は User-Agent 必須、10 req/s 未満
- 設定はすべて YAML/CSV に外出しし、コードにハードコードしない
- 各フェーズは冪等。同じ入力で2回実行したら同じ出力

完了条件:
- `mailauth p1-population --config configs/populations/jp-prime.yaml --run 2026-08`
  が動き、entities.parquet と _manifest.json が生成される
- 件数が 1,450〜1,650 の範囲に入る
- entity_id が将来 LEI ベースに移行できるスキーマになっている
  （population_ids は最初から配列で持つこと。Sprint 1.5 で global500 と
    重複排除するため）
- コンソールを起動すると P1 の実行結果が画面1に表示される
- pytest が通る

DESIGN.md に書かれていない判断が必要になったら、実装を止めて質問してください。
```

---

## 11. 未解決事項

実装と並行して解決すべきもの。詳細はリサーチ統合ドシエの付録を参照。

| # | 項目 | 影響するフェーズ | 解決手段 | 優先度 |
|---|---|---|---|---|
| 1 | 国内ベンダーの固定ホスト名 | P6 | **実測からの帰納的発見**（コンソール画面5） | 高 |
| 2 | Tatang 辞書 GPL-3.0 の波及 | P4（L3） | 法務確認。それまで L3 は無効 | 中 |
| 4 | Global 500 の非上場・非米国企業の identity | P1 | LEI 欠損時のフォールバック設計。実測で欠損率を見てから | 中 |
| 5 | 第2層を個人名義で出すことの整理 | P8 | 所属との独立性の明記、必要なら法務相談 | 中 |
| 6 | 国内ベンダーの DMARC rua 宛先 | P6 | 実測（`dmarc25.jp` 以外は未確認） | 低 |

### 決定済み（v0.1 からの変更）

| 旧・未解決事項 | 決定 |
|---|---|
| 展開の深さ | **網羅展開。上限30,000。階層A/Cで計測を分ける** |
| 公開主体 | **個人研究・個人アカウント** |
| 市場区分 | **内部フィルタに限定。JPX 非依存を維持** |
| DANE | **計測する。主要指標にはしない** |
| パークドメイン分類 | **指標化する** |
| DKIM L3 辞書 | **当面使わない。UI に「開発予定」と表示** |
| 第2層 | **実施する**（Sprint 9） |
| 通知キャンペーン | **実施する**（Sprint 9） |
| バックフィル（有償） | **当面やらない。**OpenINTEL は非商用で利用可 |
| 出力データのライセンス | **CC0 1.0。**運営者が決定（2026-08）。`LICENSE-DATA` に正文を同梱 |

### 出力データのライセンス（決定済み）

**CC0 1.0 に決定した**（2026-08、運営者の判断）。正文は `LICENSE-DATA` に同梱し、
`configs/publish.yaml` の `tier1.license` と公開サイトの表示が一致していることを
`tests/test_compliance.py` が検査する。

判断の経緯として、検討した三つの選択肢を残す。

| 選択肢 | 帰結 |
|---|---|
| **CC0** | 引き継ぎ可能性が最大。M-Lab 方式。バス係数1のリスクへの最良の保険 |
| CC BY 4.0 | 帰属表示を要求。Cloudflare Radar の embed 相当 |
| CC BY-NC 4.0 | 非商用限定。ただし自身が非商用なので整合はする |

**CC0 を採用した。** 個人研究でバス係数が1である以上、「自分が続けられなくなっても誰かが引き継げる」ことが最大の保険になる。ただし OpenINTEL 由来データを混ぜる場合、CC BY-NC-SA の継承条項が波及しうるため、OpenINTEL 由来の系列は別ファイル・別ライセンスで分離して配布すること。

---

*本書はリサーチ統合ドシエ（DR-01〜DR-18）を前提とする。数値の出典と確度は同ドシエを参照のこと。*
