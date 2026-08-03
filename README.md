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

P1〜P6 まで実装済み。

| フェーズ | 内容 | 状態 |
|---|---|---|
| P1 母集団確定 | 企業リストの取得と identity 付与 | **実装済**（日本・EDINET 経路） |
| P2 ドメイン候補生成 | 企業から関連ドメイン群を展開 | **実装済** |
| P3 メールドメイン確定 | 候補から送信ドメインを絞り確度付与 | **実装済** |
| P4 DNS計測 | MX/TXT/SPF/DKIM/DMARC 等の生取得 | **実装済** |
| P5 パース | 生レスポンスの構造化と仕様準拠の解釈 | **実装済** |
| P6 推察 | メール基盤・製品の推定、パーク分類 | **実装済** |
| P7 集計 | 全社統計・業種別集計・前月差分 | 未実装（Sprint 6） |
| P8 公開 | 静的サイト生成とデプロイ | 未実装（Sprint 7） |

未実装のフェーズは、実行すると「どのスプリントで実装予定か」を添えて停止する。
空の出力を作って下流に「0件だった」と誤解させないため。

運用コンソールは画面1（パイプライン全景）、画面2（フェーズ実行）、画面5（辞書メンテナンス）
とビュー切り替えパネルまで。

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
mailauth p4-measure    --run 2026-08   # 認証レコードを取得し bronze に生保存
mailauth p4-measure    --run 2026-08 --dry-run   # クエリ量の見積りだけ出す
mailauth p4-measure    --run 2026-08 --tier A    # 階層を絞る
mailauth p4-measure    --run 2026-08 --method zdns
mailauth p5-parse      --run 2026-08   # bronze を解釈して facts.parquet を作る
mailauth p5-parse      --run 2026-08 --no-dns   # Tree Walk と rua 検証をしない
mailauth p6-infer      --run 2026-08   # 辞書と照合して基盤・製品を推定する

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

## DNS計測（P4）

確定したドメインの認証レコードを取得し、**生のまま bronze に保存**する（原則1）。

### 階層別計測

30,000ドメインすべてにフル計測をかけると GitHub Actions の6時間上限に触れるため、
P3 が付けた `measure_tier` で深さを変える。

| 階層 | 対象 | クエリ | 内容 |
|---|---|---|---|
| A | confirmed / likely | 約59 | MX/SPF/DMARC + DKIM 約56セレクタ + 対照 + MTA-STS/TLS-RPT/BIMI/DANE |
| C | parked / unknown | 5 | MX/SPF/DMARC/DMARC-sub + DKIM ワイルドカード |

`--dry-run` でクエリ量の見積りだけ出せる。実行前に上限に触れないか確認できる。

### DKIM セレクタは動的に決まる

MX と SPF を先に引いて事業者を推定し（L2）、その既知セレクタを L1 の前に置く。
MX が `*.mail.protection.outlook.com` なら `selector1` / `selector2` が最優先になる。
L3（Tatang 3,498語）は GPL-3.0 のため無効のまま。

セレクタは DNS 上で列挙できないので、**「未設定」と「既知セレクタでは未検出」は
区別する**（原則5）。実在しないセレクタを引く対照クエリを必ず投げ、応答したら
ワイルドカードDNSとして `WILDCARD_DNS` を警告する（偽陽性ガード）。

### bronze は不変・追記のみ

```
data/runs/2026-08/p4_measure/bronze/
├── method=dnspython/part-0000.jsonl.zst
└── method=zdns/part-0000.jsonl.zst
```

同じ run に対して P4 を再実行すると、既存パートには触らず次の番号に追記し、
`BRONZE_APPENDED_TO_EXISTING` を警告する。原則1（不変）と原則6（再実行可能）を
これで両立させている。やり直すなら別の `run_id` を使うこと。

**分割された TXT は連結せず character-string の配列のまま保存する。** 連結は P5 の
責務。255バイト境界で分割された SPF の末尾の空白を削ると
`"…example.com" + "-all"` = `"…example.com-all"` になり SPF として成立しなくなるため、
生データ用のモデルは空白を一切削らない。

### バックエンドはプラガブル

`--method` で切り替える。既定は `dnspython`（依存として常に入っている）、
DESIGN.md の主力は `zdns`。**無いバックエンドに黙って落ちない。** どの手法で
測ったかは成果物の意味を変えるため、暗黙の差し替えはしない。

### TCP/53 が必要

TXT レコードが多いドメインでは UDP 応答が 512 バイトを超えて truncated になり、
TCP/53 への切り替えが必要になる。**TCP/53 を通さない経路では大企業の SPF を
一切観測できない。** その場合 P3 は該当ドメインを `observed=false`（取れなかった）
として計測対象から外し、`TCP53_UNAVAILABLE` を警告する。「SPF が無い」と
読まないこと。

## パース（P5）

bronze を構造化し、仕様に照らして解釈する。**このフェーズは何度でも作り直せる。**
パーサにバグが見つかったら bronze から再実行する（原則1 の実質的な意味）。
bronze には触らない。

### SPF

- 複数の `v=spf1` は PermError（RFC 7208 §4.5）。どちらを採るかの問題ではない
- 10ルックアップ制限。カウント対象は `include` / `a` / `mx` / `ptr` / `exists` / `redirect`。
  `all` / `ip4` / `ip6` は対象外。「ルックアップを行うメカニズムの数」であり
  生成されるクエリ総数ではない
- `all` が存在すると `redirect` は無視される（RFC 7208 §6.1）
- フラット化の検出（ip4/ip6 が20件以上で include がほぼ無い）
- 動的SPF（Valimail のマクロ）の検出。静的にルックアップ数を数えても実効を表さない

### DMARC の二重計算

**受信側は依然として RFC 7489 のまま**（RFC 9990 形式で送っている大手は
United Internet のみ、全レポーターの0.6%）。したがって RFC 7489 準拠の判定が
「実際に効いている強度」に最も近い。しかし Tree Walk 差分は将来必ず顕在化するため、
**両方を計算して保持する。**

| 名目 | 修飾子 | RFC 7489実効 | RFC 9989実効 | ラベル |
|---|---|---|---|---|
| `p=reject` | rua有 | reject | reject | `enforced_reject` |
| `p=reject` | `pct=10` | quarantine | reject | `nominal_reject_weak_pct` |
| `p=reject` | `t=y` | reject | quarantine | `nominal_reject_testing` |
| `p=reject` | rua無 | reject（可視性ゼロ） | reject | `blind_reject` |
| `p=none` | rua有 | 監視のみ | 監視のみ | `monitoring` |
| `p=none` | rua無 | 実質無効 | 実質無効 | `ineffective` |

未知タグは評価しないが**生値は保持する**（round-trip のため）。
`rua` はドメイン部だけを保存する（個人情報を集めないため）。

### Organizational Domain の二重解決

PSL と Tree Walk の両方を計算し、`org_domain_divergence` を保存する。

**Tree Walk は `psd=y` / `psd=n` を含むレコードでしか停止しない**（RFC 9989）。
psd を持たないレコードで停止させると Author Domain 自身が常に Organizational
Domain になり、PSL とほぼ全件で食い違って差分の指標が意味を失う。
psd が見つからなければ判定不能とし、差分としては報告しない。

PSL の PRIVATE セクション（`s3.amazonaws.com` 等）では実際に差分が出る。

### DKIM は三値

| 状態 | 意味 |
|---|---|
| `detected` | 既知セレクタで検出できた |
| `not_found_in_known_selectors` | 既知セレクタでは見つからなかった。**「未設定」ではない** |
| `not_applicable` | そもそもセレクタを投げていない（階層C） |

セレクタは DNS 上で列挙できないので、検出できなかったことは「無い」の証明に
ならない。`p=` が空なら失効（RFC 6376 §3.6.1）。対照クエリが応答したら
`dkim_wildcard_suspect` を立て、検出結果を信用しない。

### 成熟度ステージ

MTA-STS / BIMI / DANE はいずれも DMARC を事実上の前提とするため、単純加算すると
下位項目を二重評価する。順序性を反映した階層で表す（Stage 0〜4）。

DANE は **DNSSEC 署名の有無を必ず併記**する。「TLSA はあるが親ゾーンが未署名で
実効しない」という誤設定（`dane_orphan`）を検出できる。

## 推察（P6）

fact からメール基盤とセキュリティ製品を推定する。**推定には必ず confidence と
evidence が付く**（原則2）。DNS は引かない。silver を読んで silver を書くので、
辞書を更新したら P6 だけを再実行すればよい。

規則は `configs/fingerprints/*.yaml` と `configs/vendors/dmarc_rua_vendors.yaml`
にあり、**コードには規則を1つも書かない**（原則7）。読み込み時に正規表現の
コンパイル、`record` 種別、`id` の一意性を検証する。壊れた規則を黙って無視すると
「一致0件」と区別が付かなくなるため、例外にして止める。

### 二段推定 ── 単一ベンダーに丸めない

ゲートウェイ型製品は MX を自社に向けさせるので、MX だけでは背後の実基盤が
見えない。MX が `security_gateway` に一致しても、SPF include や DKIM CNAME から
導いた `mail_platform` を別カテゴリとして残す。

| 観測 | 出力 |
|---|---|
| MX=`*.securemx.jp` + SPF `spf.protection.outlook.com` | security_gateway=IIJ、mail_platform=Microsoft |
| MX=`*.iphmx.com` + TXT `MS=` + DKIM →`onmicrosoft.com` | security_gateway=Cisco、mail_platform=Microsoft（high） |

確度は辞書の宣言値を出発点に、証拠の強さで上下する。

- DKIM CNAME があれば high。署名基盤は最も実基盤に近い
- 強い証拠（MX / SPF include）が2種類以上そろえば1段上げる
- 所有権確認 TXT しか無ければ low

IIJ セキュアMX の署名ドメイン `dxg.dox.jp` はアライメント不可のゲートウェイ独自
ドメインなので、`security_gateway` の規則として登録している。これを
`mail_platform` と読むと「IIJ がメール基盤」という誤った推定になる。

### 所有権確認 TXT は3か月見てから降格する

`MS=` や `google-site-verification=` は削除されずに残りやすい。対応する
MX / SPF / DKIM の裏付けが無ければ「過去の痕跡」だが、**即座に stale にはしない**。
月次差分での観測が最も確実な判別手段なので、3か月連続で裏付けが出なかった場合に
降格する。連続月数は `stale_streak_months` に持ち、前月の出力から引き継ぐ。

裏付け条件が辞書に書かれていない規則は「判定していない」として扱う。
「裏付けが無い」と混ぜない（原則5）。

### パークドメイン分類 ── 本システム固有の指標

網羅展開したドメインの大半は送信に使われていない。その中で「適切に固められた
もの」と「単に放置されたもの」を区別する。送信実績がなく監視もされていない
ドメインは、なりすましの理想的な出発点になるため。

| 分類 | MX | SPF | DMARC |
|---|---|---|---|
| `hardened_parked` | Null MX | `-all` | `p=reject` |
| `defended_parked` | 無し / Null MX | `-all` / `~all` | reject / quarantine |
| `intentional_no_send` | 無し | `-all` / `~all` | 何でも |
| `neglected` | 無し | 無し | 無し |
| `active_sending` | 有り | 有り | ─（分類対象外） |
| `inconsistent` | 矛盾 | | |

これで次の二つを分けて示せる。

- **送信ドメインの強制率**: `active_sending` のうち enforcement に達している割合
- **非送信ドメインの防御率**: パークドメインのうち `hardened_parked` + `defended_parked` の割合

**観測できなかったドメインは分類しない。** SERVFAIL で何も取れなかったドメインを
`neglected`（放置）と呼ぶのは事実の捏造である（原則5）。

### 検出できない製品を「使っていない」と数えない

Abnormal Security、Avanan、Darktrace EMAIL、Vade for M365、Perception Point、
Agari は MX を変更せず API / OAuth で連携するため、**原理的に DNS に痕跡を
残さない**。セキュリティ製品が1件も検出できなかったドメインには
`undetectable_reason=api_mode_product` の行を1件出す。manifest の注記だけにすると
P7 / P8 に渡った時点で消えてしまうため、データ側に残す。この番兵行はベンダー
シェアの分母には入れない。

### 未知 MX ホストが辞書を育てる

国内ベンダーの固定ホスト名は公開情報から特定できないものが多い。実測データからの
帰納的発見でしか埋まらないので、辞書に一致しなかった MX ホストを**登録ドメイン
単位で頻度順に集約**して manifest に出す。顧客別ホスト名を1件ずつ数えても手がかりに
ならないため。

コンソールの画面5（辞書メンテナンス）からその一覧を見て、ベンダーを手入力して
YAML に追記し、その場で P6 だけ再実行して効果を確認できる。追記は行単位の挿入で
行い、既存のコメント（判定根拠・出典・注意書き）を消さない。書き戻した内容を
読み直して検証し、通らなければ元に戻す。

受け入れ基準は「推定が付かないドメインが 20% 未満」。超えると manifest に
`NO_INFERENCE_RATE_HIGH` の警告が出る。

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
