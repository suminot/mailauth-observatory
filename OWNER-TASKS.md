# 運営者の作業一覧

コードで解けない作業をここに集めた。**実装側の残件は無い**（DESIGN.md 第8章の
Sprint 1〜10 は実装済み。第9章の受け入れ基準は skip 1件を除いて機械検査）。

---

## まず今日やること（1件だけ）

**以下の9件を今日やろうとしないこと。** 依存関係があるので、実際に着手
できるのは常に1〜2件である。9件が並んでいると全部が未完了に見えるが、
大半は「まだやらなくていいもの」で、1 を通すまで判断材料すら無い。

いま何をすべきかは機械が答える。

```bash
mailauth doctor
```

**次の一手を1つだけ出す。** 持っている鍵と生成物の状態から判断するので、
状況が変われば答えも変わる。GitHub Actions の実行ログ先頭にも同じものが出る。

### 鍵がまだ1つも無いなら

**米国母集団は登録も申請も要らない。** 出典が SEC EDGAR（パブリックドメイン）と
Wikidata（CC0）なので、必要なのは `MAILAUTH_CONTACT_EMAIL` ひとつ ──
あなたのメールアドレスである（SEC が User-Agent に連絡先を求めるため）。

1. GitHub → Settings → Secrets and variables → Actions → New repository secret
2. Name `MAILAUTH_CONTACT_EMAIL` / Value あなたのメールアドレス
3. Actions → 月次計測 → Run workflow
   - population: `configs/populations/us-all-listed.yaml`
   - limit: `50`

**所要 約5分＋待ち時間30分。** これで八工程が端から端まで通り、サイトの
生成物まで出る。国内側で要るのは gBizINFO のトークン1つだけで、
それはそのあとでよい（**EDINET の鍵は母集団の取得には要らない** ── 下記）。
gBizINFO は申請制で待たされるが、**待っている間に他が止まらない**のが
この順序の理由である。

---

## 全体の一覧

順序に意味がある。**1 を先にやると 5・6・7 の判断材料が出る。**

| # | 作業 | これが無いとどうなるか | 依存 |
|---|---|---|---|
| [1](#1-初回の全件計測) | 初回の全件計測 | 辞書が育たない。判断材料が出ない | API キー |
| [2](#2-退避先の設定r2--副) | 退避先の設定 | bronze を失うと過去の再解釈ができない | ─ |
| [3](#3-cloudflare-pages-への接続) | Cloudflare Pages | サイトが公開されない | ─ |
| [4](#4-月次実行の確認) | 月次実行の確認 | 気付かないまま止まる | 1〜3 |
| [5](#5-tatang-辞書-gpl-30-の判断) | Tatang GPL-3.0 の判断 | DKIM 検出率が上がらない可能性 | **1** |
| [6](#6-未知-mx-ホストの同定) | 未知 MX ホストの同定 | 推定率が上がらない | **1** |
| [7](#7-第2層個社名付き明細を出す判断) | 第2層を出す判断 | 個社明細が出せない | 1・3 |
| [8](#8-zenodo-で-doi-を取る) | Zenodo で DOI | 学術的な参照可能性が無い | 1 |
| [9](#9-認証情報を組織で管理する) | 認証情報の組織管理 | バス係数1が残る | ─ |

---

## 1. 初回の全件計測

**最優先。** これを1回通すと 5・6 の判断材料が同時に出る。

### 母集団によって必要な鍵が違う

**全部を揃えてから始める必要は無い。**

| 母集団 | 要る鍵 | 取得の手間 |
|---|---|---|
| `us-all-listed` | `MAILAUTH_CONTACT_EMAIL` のみ | **登録不要。今日できる** |
| `jp-all-listed` | `MAILAUTH_GBIZINFO_TOKEN` のみ | 申請制で待つ |

**EDINET の API キーは母集団の取得には要らない。** コードリストの配布物が
認証の要らない静的な zip だからで、2026-09 の実行が鍵なしで 11,386 件を
取得している。鍵が要るのは書類取得 API（有報の本文）を使う段で、いまは使っていない。

米国側を先に通せば、八工程・サイト生成・辞書の飽和曲線まで全部確認できる。
国内側の鍵が揃うのを待つ必要は無い。`mailauth doctor` が現状で何が回せるかを出す。

### 必要な API キー

GitHub の Settings → Secrets and variables → Actions に入れる。

| secret | 用途 | 取得元 | 無いとどうなるか |
|---|---|---|---|
| `MAILAUTH_EDINET_SUBSCRIPTION_KEY` | 書類取得 API（現状は未使用） | [EDINET API](https://api.edinet-fsa.go.jp/api/auth/index.aspx?mode=1)（無料・要登録） | コードリストは鍵なしで取れるので**いまは影響しない** |
| `MAILAUTH_GBIZINFO_TOKEN` | 公式サイト URL | [gBizINFO](https://info.gbiz.go.jp/api/index.html)（無料・申請制） | `official_domain` が全社欠損し、**P2 の候補生成が起点を失う** |
| `MAILAUTH_HOUJIN_BANGOU_APP_ID` | 商号の裏取り | [国税庁](https://www.houjin-bangou.nta.go.jp/webapi/)（無料） | 裏取りをスキップ（動く） |
| `MAILAUTH_CONTACT_EMAIL` | SEC の User-Agent | あなたの連絡先 | 米国母集団が**理由を添えて停止**する |

`MAILAUTH_CONTACT_EMAIL` は**実在する連絡先を入れること。** SEC は User-Agent に
連絡先を含めることを必須としており、偽の値を送るのは規約違反である。だから
未設定のとき動かないようにしてある（黙って偽装しない）。

### 実行

Actions → 「月次計測」→ Run workflow。

**初回はいきなり全件を回さないこと。** CT ログ取得が支配的で時間がかかる。

```
1回目  limit = 50    → 30分程度。通ることの確認
2回目  limit = 500   → 経過時間を測る
3回目  limit 空欄     → 全件
```

### 所要時間の見積り

crt.sh は qps 0.5 に絞っている（相手への礼儀なので外さない）。

- 3,830社 ÷ 0.5 = **最低約2.1時間**
- 大企業は SAN が数千件返る（`fastretailing.com` で 5,161 件）ので実際はさらに伸びる
- **GitHub Actions の1ジョブ上限は6時間**

6時間に当たったら `limit` を刻んで複数回に分ける。同じ月のキャッシュは
`data/cache/crtsh/month=YYYY-MM/` に残るので、2回目以降は速い
（月が変われば取り直す ── CT ログは追記されるため）。

### 完了の確認

```bash
mailauth status --run 2026-08          # 八工程の状態
mailauth run-report --run 2026-08      # manifest を1枚に
```

`gold/month=YYYY-MM/` がコミットされ、`runs/YYYY-MM.md` が残っていれば成功。

### 見ておくべき警告

| 警告 | 意味 |
|---|---|
| `TCP53_UNAVAILABLE` | 実行環境が TCP/53 を通していない。**該当ドメインは未観測**（SPF が無いのではない） |
| `ACCEPTANCE_COUNT_OUT_OF_RANGE` | 取得件数が想定外。母集団の取得に失敗している疑い |
| `UNALIGNED_RUA_TARGET` | 自社ドメインでない rua 宛先。**6 の材料** |
| `UNKNOWN_MX_HOSTS` | 辞書に無い MX ホスト。**6 の材料** |

---

## 2. 退避先の設定（R2 ＋ 副）

bronze / silver は Git に入れない（履歴肥大化の回避）。**bronze を失うと過去の
再解釈ができなくなり、原則1（生データは不変）の意味がなくなる。**

### secrets

| secret | 主（R2） | 副 |
|---|---|---|
| エンドポイント | `R2_ENDPOINT` | `BACKUP_ENDPOINT` |
| バケット | `R2_BUCKET` | `BACKUP_BUCKET` |
| アクセスキー | `R2_ACCESS_KEY_ID` | `BACKUP_ACCESS_KEY_ID` |
| シークレット | `R2_SECRET_ACCESS_KEY` | `BACKUP_SECRET_ACCESS_KEY` |

R2 は Cloudflare ダッシュボード → R2 → Manage R2 API Tokens。

**副は別事業者・別アカウントにすること**（Backblaze B2 / Wasabi / AWS S3 など）。
DESIGN.md 第9章が「bronze のバックアップを **R2 以外に**持つ」ことを持続性の
受け入れ基準にしている。同じ事業者に2つ置いても、アカウントを失う事故で
同時に失われるので持続性は上がらない。

### 副が無いとどうなるか

**月次実行は止まらない。** ただし実行ログに毎回こう出る。

```
⚠ 退避先が 1 か所しかない。DESIGN.md 第9章は 2 か所（R2 以外の保管先）を
  求めている。未設定の宛先: backup。**送れていても持続性の基準は満たしていない**
```

送信が成功していても基準未達である旨を言う設計にしてある（送れたことと
持続性は別）。

---

## 3. Cloudflare Pages への接続

### 3-1. Pages プロジェクトを作る

Cloudflare ダッシュボード → Workers & Pages → Create → Pages。
**Git 連携ではなく Direct Upload** を選ぶ（デプロイは GitHub Actions が押し出す）。

### 3-2. secrets を入れる

| secret | 取得元 |
|---|---|
| `CLOUDFLARE_API_TOKEN` | My Profile → API Tokens。**Pages の編集権限**を付ける |
| `CLOUDFLARE_ACCOUNT_ID` | ダッシュボード右側に表示される |
| `CLOUDFLARE_PAGES_PROJECT` | 3-1 で付けたプロジェクト名 |

### 3-3. 動作

`main` への push で `.github/workflows/deploy.yml` が動く。
**secrets が揃うまでは何もしない**（必要なものを実行サマリに書いて終了する）。

デプロイの前に必ず `mailauth p8-publish` が走る。ビルドだけしてデプロイすると
第1層の列フィルタと語彙検査を素通りするため、順序をテストで固定している。

### Cloudflare 側でやることはこれだけ

計算は全部 GitHub Actions で行う。**Cloudflare 上で操作するものは無い。**
Pages は置き場所、Access は認証、R2 は退避先である。

---

## 4. 月次実行の確認

毎月1日 02:00 UTC に動く。

### 60日で止まることへの対策は入っている

GitHub Actions は**60日間リポジトリに活動が無いと schedule を止める。**
データが変わらなかった月でも `--allow-empty` でコミットするようにしてある
（実行した記録そのものが活動になる）。加えて CI にも毎月1日 03:00 の cron がある。

### それでも人が見るべきもの

月に1回、次を確認する。

1. Actions に「月次計測」の実行があるか（**無ければ schedule が止まっている**）
2. `runs/YYYY-MM.md` の工程レポート
3. `runs/YYYY-MM-worklist.md`（未知 MX ホストの作業リスト → 6）
4. `site/src/changelog.md` の「解釈」欄が空いている月がないか

4 が重要である。**版が上がったことは機械が検出するが、それが数字にどう
影響したかは人が書く。** 空欄が残っていること自体が、書くべきことが
残っている印になる。

---

## 5. Tatang 辞書 GPL-3.0 の判断

**1 を終えてから判断する。** 順序を逆にすると、価値の分からないものに
法務費用を払うことになる。

### 何が問題か

DKIM セレクタ辞書 L3 の想定出典（Tatang の3,498語）が GPL-3.0 である。
本リポジトリは Apache-2.0 なので、取り込んで配布すると GPL-3.0 §5 が
結合著作物全体を GPL-3.0 で配布せよと要求する。Apache-2.0 → GPL-3.0 は
一方通行で、逆はできない。**リポジトリ全体が GPL-3.0 になる。**

ただしその前に「そもそも著作物か」がある。`s1` `selector1` のような文字列の
リストに著作権が及ぶかは自明でない（米国なら Feist、日本なら創作性の要件）。
選択・配列に創意があれば保護されうるので灰色である。

### 判断の手順

1 の実行後、DKIM セレクタの飽和曲線を見る。**CLI には無い。**
コンソール画面5（辞書メンテナンス）を開くか、直接呼ぶ。

```bash
# コンソールを立ち上げて画面5を見る
uvicorn console.backend.main:app --port 8000
# → http://localhost:8000 の「辞書メンテナンス」タブ

# または直接
python -c "
from mailauth.p4_measure.saturation import from_bronze
from mailauth.paths import bronze_dir
d = from_bronze(bronze_dir('2026-08')).to_dict()
print('検出できたドメイン:', d['detected_domains'])
print('上位10セレクタでの網羅率:', d['coverage_at_10'])
print('9割に要したセレクタ数:', d['selectors_for_90pct'])
print('99%に要したセレクタ数:', d['selectors_for_99pct'])
"
```

**読み方に注意がある。** この指標は辞書の並び順に依存する。
「何個目で飽和したか」ではなく**「最後の何割が何件しか稼いでいないか」**を見る。

- **上位10セレクタで99%取れているなら、L3 の議論自体が不要。** 「使わない」で確定
- 明確に取りこぼしているなら、下記から選ぶ

| 案 | 帰結 |
|---|---|
| A. 使わない | 法務費用ゼロ |
| B. 取り込まず実行時に取得 | 配布しないので copyleft の発火条件を踏まない。`configs/dkim_selectors/l3_extended.txt` に既にこの方針が書かれている |
| C. 自前観測で作り直す | ライセンス問題が消える。プロジェクトの方針と一致。時間はかかる |
| D. 作者に許諾を求める | 一番きれい。返事が来るかは不明 |

現状は A で固定してある（`l3_on_miss: false`、`l3_status: planned`、
`l3_extended.txt` は意図的に空）。**変えるまで何も起きない。**

---

## 6. 未知 MX ホストの同定

DESIGN.md 第11章の未解決事項1（優先度**高**）。仕組みは実装済みで、
埋めるのは人の作業である。

### 毎月の手順

```bash
cat runs/YYYY-MM-worklist.md
```

「手を付けるもの」の上から順に、登録ドメインを調べて製品名を特定する。

| 結果 | どうするか |
|---|---|
| 製品名が分かった | `configs/fingerprints/*.yaml` に規則を足す（コンソール画面5からでも可） |
| 調べたが分からない | `configs/worklist/unidentified_hosts.yaml` に理由付きで記録 |

**「調べたが分からなかった」は結論であって、無かったことにしない。**
記録すると作業リストから外れるが件数は残る。理由は
`self_hosted` / `reseller` / `no_public_info` / `dead_domain` から選ぶ。

3か月以上そのままのものには ⚠ が付く。**同定するか、同定できない理由を
記録するかのどちらかをする。**

### rua ベンダーも同じ

`UNALIGNED_RUA_TARGET` に出たドメインは、ベンダー名が分かれば
`configs/vendors/dmarc_rua_vendors.yaml` に足す。P6 の `dmarc_vendor` 推定が埋まる。

**名前が分からなくても誤帰属は起きない**（自社ドメインでない rua 宛先は
候補にしない構造的な判定が入っている）。急がなくてよい。

現在確認済みで名前が未確定のもの:

| ドメイン | 観測された企業 |
|---|---|
| `smtps.jp` | ajinomoto.com, mufg.jp |
| `teams.ms` | ajinomoto.com |

---

## 7. 第2層（個社名付き明細）を出す判断

**ここが一番慎重に進めるところ。** 訂正期間を経ていない個社明細を公開すると
取り返しがつかない（キャッシュもインデックスも残る）。

### アクセス制御（Cloudflare Access ＋ Entra ID）

第2層は認証の内側にしか置けない。**`mkilabo.com` のアカウントだけを通す**設定は
Cloudflare Zero Trust 側に作る。リポジトリ側は「掛かっていることの検査」を持つ。

#### 0. 先に確認すること

**Entra のテナント管理者権限が要る。** アプリの登録自体は一般ユーザーでもできる
設定のテナントがあるが、Microsoft Graph の権限に**管理者の同意**を与える操作は
管理者にしかできない。自分で完結しないなら情シスへの依頼が先に立つ。

権限が下りないうちに動かしたい場合は、Cloudflare だけで完結する
**One-time PIN**（メールに6桁のコードを送る方式）がある。切り替えは
`configs/publish.yaml` の `idp` を書き換えるだけで、**検査の仕組みは変わらない**
（どちらもログイン画面への転送を見ている）。ただし OTP には「テナント内」という
下限が無いので、**`Emails ending in @mkilabo.com` の条件が必須**になる。

#### 1. Entra ID にアプリを登録する

Microsoft Entra 管理センター → **アプリの登録** → 新規登録

| 項目 | 値 |
|---|---|
| 名前 | 任意（例 `mailauth-observatory`） |
| サポートされるアカウントの種類 | **この組織ディレクトリのみ**（シングルテナント） |
| リダイレクト URI | Web / `https://<チーム名>.cloudflareaccess.com/cdn-cgi/access/callback` |

作成後に控えるもの:

- **アプリケーション (クライアント) ID**
- **ディレクトリ (テナント) ID**
- **クライアントシークレット**（証明書とシークレット → 新しいクライアントシークレット）

API のアクセス許可に Microsoft Graph の `email` / `openid` / `profile` /
`User.Read` を入れ、**管理者の同意**を与える。

#### 2. Cloudflare Zero Trust に Entra を足す

Zero Trust ダッシュボード → **Settings** → **Authentication** → Login methods →
Add new → **Azure AD**。1 で控えた3つを入れて保存し、**Test** で通ることを確認する。

#### 3. Access アプリケーションを作る

Zero Trust → **Access** → Applications → Add an application → **Self-hosted**

| 項目 | 値 |
|---|---|
| Application domain | `<プロジェクト名>.pages.dev` |
| Path | `companies`（第2層だけを閉じる場合） |

ポリシー:

| 項目 | 値 |
|---|---|
| Action | Allow |
| Include | **Emails ending in** `@mkilabo.com` |
| Require | **Login Methods** = 2 で作った Entra |

**Include を「ログイン方法 = Entra」だけにしないこと。** それだけだと、
そのテナントに招かれたゲストアカウント（外部ドメインのメール）も通る。
所属で絞るならメールドメインの条件を別に置く必要がある。

#### 4. リポジトリ側の設定

`configs/publish.yaml` の `access`:

```yaml
access:
  provider: cloudflare_access
  idp: entra_id
  allowed_email_domains: [mkilabo.com]
  protected_paths: []          # 試験中にサイト全体を閉じるなら "/" を足す
  verify_base_url: https://<プロジェクト名>.pages.dev
```

`verify_base_url` を入れると検査できるようになる。

```bash
mailauth access-check
```

対象パスを**認証なしで叩いて、実際に弾かれること**を確かめる。

| 表示 | 意味 |
|---|---|
| ○ | Access / Entra のログインへ転送された、または 401・403 |
| × | **認証なしで本文が返った。公開されている** |
| ? | 確かめられなかった。**「守られている」ではない** |

#### なぜ検査するのか

`tier2.access_control_configured` は**人が YAML に書き込む真偽値**であり、
認証が掛かっていることの証拠ではない。Access アプリを作る前に `true` にしても、
ポリシーの Path を間違えても `true` のままになる。個社明細の公開は
取り返しがつかない（キャッシュもインデックスも残る）。

**第2層を出す実行では、P8 が同じ検査を必ず通す。** 確かめられなければ止まる。

> **試験中にサイト全体を閉じる場合。** `protected_paths` に `"/"` を足せば
> Pages の URL 全体が Entra の内側に入る。ただし DESIGN.md は第1層を
> 無条件公開と定めているので、**公開前に外すこと。** 外し忘れに気付けるよう、
> `"/"` が入っている間は検査と P8 が毎回警告を出す。

### 前提の設定

`configs/publish.yaml` の `tier2`:

```yaml
tier2:
  enabled: false                      # 最後に true にする
  notified_on: null                   # 通知を送った日（YYYY-MM-DD）
  access_control_configured: false    # Cloudflare Access を設定したら true
  correction_contact: null            # 訂正申告の窓口。必須
```

`notify`（通知の設定）:

```yaml
notify:
  sender_domain: null                 # あなたのドメイン
  sender_dkim_selectors: []           # 自分のセレクタ（自分だけが知っている）
  detail_url: null                    # https://<あなたのドメイン>/companies/{domain}
  method_url: null                    # https://<あなたのドメイン>/methodology
  optout_contact: null                # 「今後不要」の返信先
```

**`detail_url` と `method_url` は差出人と同じドメイン上でなければ文面を作らない。**
日本市場では「不審メール扱いされるリスク」が最初の障壁なので、受信側が
ドメイン名を直接入力して到達できることを要件にしている。

### 順序

1. **自分の SPF/DKIM/DMARC を完全準拠にする。** これが通らないと計画を作れない
   （SPF あり・妥当・`-all` か `~all`、DMARC あり・強制・`rua` あり、DKIM 検出）
2. `mailauth notify-plan --run YYYY-MM --stdout` で文面と宛先を**目で確認する**
3. 送る（**送信経路はコードに無い。** 手段はあなたが選ぶ）
4. `tier2.notified_on` に送った日を入れる
5. Cloudflare Access のポリシーを `/companies` に掛け、
   `access_control_configured: true` にする
6. **最低30日（推奨60日）待つ**
7. `tier2.enabled: true` にする

6 の日数判定はコードが行う。満たしていなければ P8 が理由を列挙して停止する
（**運営者の判断で短縮できないようにしてある**）。

### 送る前に読んでおくこと

DR-18 の実測値。**送れば直るという前提で運用を組むと、結果を見て
「失敗した」と誤読する。**

| 指標 | 実測 |
|---|---|
| security.txt 普及率（Fortune 500） | 約4% |
| RFC 2142 エイリアスの到達率 | 24.16%（バウンスしないだけ） |
| SPF 不備通知の2週間後是正率 | **3.3%** |
| 法的フレーミング＋郵送 | 76.3% |

### 訂正申告が来たら

`configs/corrections/corrections.yaml` に記録する。
**受付から48時間以内の審査を目標、72時間を上限。** 超過は
`mailauth corrections` が検出して印を付ける。

審査中のものも、訂正しなかったものも訂正履歴ページに載せる。
訂正した分だけを載せると、申告が何件あってどう扱われたのかが分からなくなる。

計測対象から外す依頼は `configs/domains/excluded.csv` に足す。
以降そのドメインには DNS の問い合わせを1本も出さない。

### 未解決事項5（個人名義で出すことの整理）

第2層を個人名義で出す場合、名誉毀損の抗弁における「公益目的」の立証が
組織運営より弱くなる（DESIGN.md 1.4）。対策として実装済みのもの:

- 方法論・コード・データの完全公開
- 事前通知と訂正期間
- 訂正窓口の常設と訂正履歴の公開
- 表現規約の機械検査（断定的語彙・順位付けの禁止）

**サイトに所属と独立性を明記し、営業要素を一切含めないこと**は
あなたが書く部分である。必要なら法務相談。

---

## 8. Zenodo で DOI を取る

DESIGN.md Sprint 10。学術的な参照可能性の確保で、バス係数1への対策でもある。

1. [Zenodo](https://zenodo.org/) に GitHub アカウントでログイン
2. Settings → GitHub → このリポジトリを ON
3. GitHub でリリースを作る（例 `v0.1.0`）→ Zenodo が自動で DOI を発行
4. 発行された DOI バッジを README に貼る

**1 を終えて gold が1か月分でも入ってからにする。** 空のデータセットに
DOI を付けても引用できない。

データのライセンスは CC0 1.0 に決定済み（`LICENSE-DATA`）。
コードは Apache-2.0（`LICENSE`）。

---

## 9. 認証情報を組織で管理する

第9章の受け入れ基準で唯一 skip のままの項目。
**個人研究として個人アカウントで運営している前提と両立しない。**
DESIGN.md 1.4 がバス係数1のリスクとして明示している。

いま実装側でできているのは次まで。

- 認証情報をハードコードしない（`.env` / GitHub secrets からのみ読む）
- `.env.example` に**各キーの取得元を明記**（引き継ぐ人が再発行できる）
- コードは Apache-2.0、データは CC0（誰でも引き継げる）

組織アカウントに移すか、個人のまま続けるかはあなたの判断である。
移す場合、secrets の移行と Cloudflare / Zenodo の所有者変更が伴う。

---

## 任意: 市場区分で絞る

`jp-prime` / `jp-standard` / `jp-growth` は**対応表を置いたときだけ動く。**
EDINETコードリストは市場区分を持たず、JPX の `data_j.xls` は
商用二次利用が規約で禁止されているので使わない方針である。

```yaml
# configs/populations/jp-prime.yaml
source:
  market_filter:
    segment_source: manual_csv
    segment_map: configs/populations/_segments/jp-tse.csv
```

書式は [`configs/populations/_segments/README.md`](configs/populations/_segments/README.md)。

**これは計測対象を絞る設定ではない。** 全上場を測ったまま各社にラベルを
付けるだけで、絞るのはビューの役目である。

対応表が無い状態で `jp-prime` を見ると総数0になるが、これは「該当企業が無い」
ではなく「区分を判定できない」である。CLI もコンソールもその区別を警告で
明示する。**総数0を該当なしと読まないこと。**

有価証券報告書の表紙【上場金融商品取引所】を XBRL から読んで自動生成する案は
未実装である（EDINET の書類取得が必要で、実データ検証ができていない）。

---

## 変えてはいけないもの

`tests/test_compliance.py` が CI で機械的に検査している。**外すと落ちる。**

- **JPX の `data_j.xls` は使わない**（非商用であっても方針として）
- **fortune.com はスクレイピングしない**
- **EDINET はスクレイピングしない。API 経由のみ**
- **DKIM 辞書 L3 は無効のまま**（5 の判断が済むまで）
- **認証情報をハードコードしない**
- **個人名を含むメールアドレスを収集・保存しない**（rua はドメイン部のみ）
- **`market_segment` を gold / 公開サイトに出さない**（内部の集計軸専用）
- **第2層は事前通知から最低30日 ＋ アクセス制御が揃うまで出さない**
- **公開サイトの表現規約**（断定的語彙・総合順位・A〜F グレードの禁止、
  限界の常時表示、事実と推察の視覚的分離、赤の面積の最小化）
- **通知モジュールに送信経路を持たせない**
- **オプトアウトと計測除外に期限を設けない**（断りは恒久的）
