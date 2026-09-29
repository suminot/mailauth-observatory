# メール基盤・前段製品の推定 設計メモ

DESIGN.md の P6（推察）を、**「どの企業が何を使っているか」を数えられる形**に
するための設計。2026-09-29 の調査に基づく。

**この文書は確定仕様ではない。** 決まっていないこと・推察でしかないことを
そのまま残してある。実装に入る前に、印の付いた箇所を潰すこと。

## 印の意味

| 印 | 意味 |
|---|---|
| **実測** | 2026-09-29 に DNS / HTTP を実際に引いて確かめた |
| **公式** | ベンダーの公式文書に記載がある |
| **推察** | 裏が取れていない。実装の根拠にする前に確かめること |

---

## 1. 何を数えるか（運営者の判断・確定済み）

| 項目 | 決めたこと |
|---|---|
| 単位 | **企業数とドメイン数の両方**を出す |
| 「使っている」の定義 | **3つを別々に出す**（下記 ①②③） |
| 併用 | **層で分ける**（実基盤 / 前段を別軸） |
| 観測できなかったもの | **全体の何% と 判明分の何% の両方**を出す |

### 1.1 三段階の定義

```
① 受信している    外部から来たメールが、最終的にその基盤に入る
② 送受信のいずれか 前段ゲートウェイの背後を含め、送信か受信のどこかで通る
③ テナントがある   契約は存在する（メールは他社かもしれない）
```

包含は **① ⊂ ② ⊂ ③** が原則だが、**①でも②でもない③**（Teams だけ契約）と、
**③の痕跡が無い②**（SPF だけ残存）の両方が実在するため、独立に数える。

**3つの差そのものが読みたい数字である。** ①と③の差が「テナントはあるが
メールは他所」の規模、②と③の差が「契約はあるがメールに使っていない」の規模。
そして**①が最も堅く、③が最も水増ししやすい** ── 3つの幅が不確かさの幅になる。

### 1.2 層

国内調査で分かったこと。**「前段」は1つではない。**

```
実基盤        メールボックスがある場所（M365 / Google Workspace / CYBERMAIL …）
受信の前段    MX を握る（IIJ セキュアMX / Mimecast / Trellix …）
送信の前段    MX を握らない。送信だけ通す（HENNGE Email DLP / Active! gate SS …）
```

**実例（実測）**: アサヒグループHD は受信が Symantec（`cluster1.us.messagelabs.com`）、
送信が HENNGE（SPF に `spf.mta.hdems.com`）。**受信と送信で別ベンダーである。**

いまの `security_gateway` 単一カテゴリではこれが混ざる。**分けること。**

---

## 2. ベンダーによる検出しやすさの非対称（最重要）

**Microsoft と Google では、検出の難しさが構造的に違う。** 同じ精度で数えられる
と考えると誤る。

| | Microsoft 365 | Google Workspace |
|---|---|---|
| MX | `<domain>.mail.protection.outlook.com` — **テナント固有文字列が入る** | `smtp.google.com` / `aspmx.*` — **全テナント共通** |
| DKIM | **CNAME** でベンダー側ゾーンを指す。テナント名まで割れる | **TXT** に公開鍵のみ。**Google 側を指す文字列が1つも無い** |
| 所有権確認 | `MS=ms########` — Microsoft 専用 | `google-site-verification=` — **Google 全製品で共用。識別力なし** |
| 周辺レコード | autodiscover / SRV / enterpriseregistration | **相当物なし** |

**帰結**: Microsoft は「MX を前段に取られても DKIM CNAME でテナントが割れる」。
Google は「**MX を外されると確実な手掛かりが1つも残らない**」。

したがって **Google の②③は、Microsoft と同じ確度では出せない。** 出す数字に
その非対称を書かないと、読み手は「Google が少ない」と誤読する。

### 2.1 `gappssmtp.com` は行き止まり（実測・重要な否定的結果）

Google は DKIM 未設定時に `d=<domain>.<日付>.gappssmtp.com` で署名する。
「この名前が DNS にあればテナント確定」と考えたくなるが、**使えない。**

**実測**: `sansan-com` について 2012-01-01〜2026-09-29 の全 5,386 日を総当りした
ところヒットは3件。しかし**存在しないドメインでも同じ鍵が返る**（公開鍵の
SHA-256 が完全一致）。`*.<日付>.gappssmtp.com` は**全テナント共通のワイルド
カード**であり、テナント固有のレコードは存在しない。

`gappssmtp` が使えるのは**受信したメールの `DKIM-Signature` ヘッダを見る場合
だけ**で、DNS スキャンでは情報がゼロである。

---

## 3. 現行の設定に、いま効いている取りこぼし

**調査で見つかった中で、最も即効性がある。** これは設計ではなくバグである。

### 3.1 Microsoft の新形式（実測で裏取り済み）

Microsoft は MX と DKIM の宛先形式を変えた。**現行の設定は旧形式しか見ていない。**

```
mx.microsoft         SOA: ns1-81.azure-dns.com. azuredns-hostmaster.microsoft.com.
mail.microsoft       SOA: ns1-36.azure-dns.com. azuredns-hostmaster.microsoft.com.
```

`.microsoft` は Microsoft 自身の TLD で、両ゾーンとも Azure DNS で運用されている
（**実測**）。

| 現行 | 取りこぼすもの | 根拠 |
|---|---|---|
| `m365-mx-01`: `\.mail\.protection\.outlook\.com\.?$` | `*.mx.microsoft`（新形式） | ゾーン実在を**実測**。調査2本が独立に指摘 |
| 同上 | `*.mail.eo.outlook.com`（旧 FOPE） | worklist に実データ6件（**実測**） |
| 同上 | `ms<数字>.msv1.invalid`（所有権確認の仮 MX） | worklist に31件。**メールは流れない** |
| `m365-dkim-01`: `\.onmicrosoft\.com\.?$` | `*.<x>-v1.dkim.mail.microsoft` | 国内調査の実測で DKIM CNAME 保有 M365 ドメインの約9%。ただし `dkim.mail.microsoft` の SOA は引けず（**推察の余地あり**） |

**`msv1.invalid` は第3の状態**である ── 「M365 にドメインは登録済みだが、受信は
そこではない」。①でも②でもなく、③に近い。**未知 MX に落とさず、専用の分類を
与えること。**

### 3.2 HENNGE を丸ごと落としている（実測。**設計者の仮説が逆だった**）

当初「HENNGE One は MX を握り、背後の M365 は DKIM でしか見えない」と想定して
調査を出した。**実測は逆だった。**

```
spf.mta.hdems.com（SPF）   34件（約9%）
*.hdemail.jp（MX）          0件
```

HENNGE Email DLP は**誤送信対策＝送信側の製品**で、受信 MX を奪わない。
そして**現行の辞書は MX 側のパターンしか持っていない**ため、国内で2番目に
多い痕跡を1件も拾えていない。

### 3.3 その他、worklist の上位で正体が判明したもの

未知 MX の上位20件（計628ドメイン）のうち、**辞書に足せば消えるもの**。

| ホスト | 件数 | 正体 | 層 |
|---|---|---|---|
| `mwpremgw*.ocn.ad.jp` | 138 | NTT Com Bizメール&ウェブ プレミアム | 実基盤 |
| `*.email.fireeyecloud.com` | 74 | Trellix Email Security – Cloud（旧 FireEye ETP） | 受信前段 |
| `mxi.alpha-prm.jp` | 57 | 大塚商会 アルファメール プレミア | 実基盤 |
| `cluster*.us.messagelabs.com` | 57 | Broadcom/Symantec Email Security.cloud | 受信前段 |
| `*.kagoya.net` | 48 | カゴヤ・ジャパン | 実基盤 |
| `*.secure.ne.jp` | 46 | KDDIウェブコミュニケーションズ CPI | 実基盤 |
| `*.outlook.com` | 31 | M365（§3.1 参照。**2種類が混在**） | 実基盤 |
| `mxin*.airnet.ne.jp` | 30 | AIRnet | 実基盤 |
| `jp*-aspmx*.worksmobile.com` | 17 | LINE WORKS | 実基盤 |
| `ampub*.alpha-mail.net` | 16 | 大塚商会 アルファメール | 実基盤 |
| `mx*.active-w.net` | 15 | MXモバイリング Active! world | 実基盤 |
| `*.mailsecure.jp` | 15 | **同定できず** | — |
| `mail.system.digitalartscloud.com` | 12 | デジタルアーツ m-FILTER@Cloud | 受信前段 |
| `mx*.larksuite.com` | 12 | Lark | 実基盤 |
| `gw*.fortimail.com` | 11 | FortiMail Cloud | 受信前段 |
| `mx-proxy*.heteml.jp` | 10 | GMOペパボ heteml | 実基盤 |
| `mxjp*.nospamcloud.com` | 10 | 使えるねっと | 受信前段 |
| `mailgw*.oneoffice.jp` | 10 | TOKAIコミュニケーションズ OneOffice | 実基盤 |
| `*.sharedmail.jp` | 10 | シェアドメール（提供者未確認） | 実基盤 |
| `mx*.lolipop.jp` | 9 | GMOペパボ ロリポップ | 実基盤 |

**上位が国内サービスで占められている。** 海外製品（fireeye + messagelabs +
fortimail = 142件）より、国内の方が量が多い。

さらに `i<数字>.mailsecurity-nec.jp`（NEC メールセキュリティ、実測5件）、
`*.in.tmems-jp.trendmicro.com`（トレンドマイクロ**国内版**、実測11件）が
未登録。**国内版とグローバル版で綴りが違う**（`tmems-jp` / `tmes`）。

---

## 4. 証拠の強さ

DESIGN.md P3/P6 の序列 `DKIM_CNAME >= MX > SPF_INCLUDE > VERIFICATION_TXT` は
今回の調査でも支持された。ただし**強さと残りやすさが逆相関する**という、
もっと重要な性質が分かった。

| 信号 | 強さ | 解約後の残りやすさ | 理由 |
|---|---|---|---|
| MX | 強 | **最も残らない** | 直さないとメールが届かない。移行時に必ず最初に直る |
| DKIM CNAME | **最強** | 残る | 消し忘れても何も壊れない |
| SPF include | 中 | やや残る | 10 lookup 制限で整理されることはある |
| 所有権確認 TXT | **最弱** | **最も残る** | 消す動機が無い。Microsoft も Google も「消してよい」としか言わない |

**つまり、確度の高い信号ほど先に消える。** 残骸だけを見て数えると、
使っていない企業を数え上げることになる。

### 4.1 `MS=xxxxx` は M365 の信号ですらない

**公式**: 同じ形式を Entra ID（無料テナント含む）・Azure・Intune のドメイン
検証が共用する。つまり **「Microsoft のテナントがある」までしか言えず、
M365 契約の証拠にならない。** しかも Microsoft は検証後の削除を公式手順の
最終ステップで推奨しているので、**残っているものは削除し忘れ**である。

### 4.2 `google-site-verification` は情報量がほぼゼロ

**実測**（上場企業50社）:

```
全50社              44社が保有（88%）
Google MX の20社    20社が保有（100%）
非 Google MX の30社  24社が保有（80%）
```

M365 を使っている企業も Proofpoint を使っている企業も持っている。
**スコアに加算しないこと。** 加算するとノイズが増えるだけである。

### 4.3 既存の stale 判定は正しいが、まだ働いていない

`verification_txt.yaml` の設計（`confidence: low` ＋ `corroborated_by` による
裏付け必須 ＋ 3か月連続で裏付けなしなら `is_stale`）は、上の性質に対して
正しい。

**ただし run 14 で `NO_STALE_HISTORY` が出ている。** 前月データが無いので
**この判定は一度も働いていない。** 効き始めるのは3か月ぶん貯まってから
（2026-11 の計測以降）。

**それまでに出す M365 / Google の数字は、残骸を含んだ上限値である。**
そう明記すること。

---

## 5. 検出できないもの（3分類）

**「未検出」と「検出できない」を混ぜない。** run 14 では 2,884 ドメインが
`SECURITY_GATEWAY_UNDETECTABLE` に落ちているが、理由が違うものが混ざっている。

### (a) 原理的に DNS に出ない — API / OAuth 連携型

配送経路に入らず、配送**後**のメールボックスを API で読む。管理者が OAuth
同意するだけで DNS は1つも変わらない。

Abnormal Security / Check Point Harmony（Avanan、Inline モード）/ IRONSCALES /
Sublime Security / Material Security / Egress Defend / Vade for M365 /
Perception Point / Darktrace EMAIL / Proofpoint（旧 Tessian）/
Cisco Secure Email Threat Defense（ジャーナリング）/ Trend Vision One
（旧 Cloud App Security、ジャーナリング）

**これらを「使っていない」と読ませてはならない。**

### (b) オンプレ／自社運用 — ホスト名が顧客ドメイン配下

**実測で468件中43件（約11%）が自社ドメインの MX。** クボタ・MUFG・大和ハウス・
伊藤忠・リクルート・DNP・東京電力・ローソン・第一生命など。

`mx3.kubota.co.jp` からは、Proofpoint on-prem なのか Cisco ESA なのか
m-FILTER なのか自作 Postfix なのか**一切分からない**。

Cisco ESA / FortiMail アプライアンス / Trend Micro DDEI / Proofpoint PPS /
Symantec Messaging Gateway / Clearswift なども同じ。

### (c) SPF が平坦化されていて読めない

SPF 統合サービスが `include` を IP 列挙やマクロに展開すると、**基盤を示す
include が消える。**

| サービス | 実測 | 形 |
|---|---|---|
| PowerSPF（PowerDMARC） | **17件** | `*.powerspf.com` |
| Valimail Enforce | 1件 | `*._spf.vali.email` |
| Proofpoint SPF Hosting | 数件 | `include:%{ir}.%{v}.%{d}.spf.has.pphosted.com` |
| `*.spf25.jp`（提供者は**推察**：TwoFive） | 4件 | `redirect=<domain>.r.spf25.jp` |

**これは「基盤が無い」ではなく「基盤が読めない」。** 専用の印を付けて、
②の判定から外すこと。

### (d) EOP standalone — MX は Microsoft だが M365 ではない

**公式**: 「Built-in security add-on for on-premises mailboxes」は MX を
`*.mail.protection.outlook.com` に向けつつ、**メールボックスはオンプレに置く**。
公式文書は「other SMTP email products」でもよいと書いている。

**DNS だけでは M365 メールボックス構成と区別できない。**

→ **①の定義の文言を決める必要がある。**「受信が M365/EOP に向いている」なら
真、「Exchange Online にメールボックスがある」なら偽。**前者を採る**ことを
推奨する（DNS で言えることに合わせる）。

---

## 6. 数え方

### 6.1 分母を2つ出す

```
全体の何%      分母 = 観測できたドメイン全部（不明を含む）
判明分の何%    分母 = 基盤を同定できたドメインだけ
```

**両方出す**（運営者の判断）。前者だけだと採用率が低く見え、後者だけだと
分母が何なのか読み手に伝わらない。

### 6.2 併用は層で分ける

1ドメインに「実基盤1つ + 受信前段1つ + 送信前段1つ」まで持てる形にする。
**同じ層の中では重複を許さない**（許すと合計が100%を超えて読めなくなる）。

同じ層で複数立った場合は証拠の強さ順（§4）で1つに決め、**落とした方は
`breakdown` に残す**（原則5：捨てたことを見えるようにする）。

### 6.3 二重計上に注意する具体例（実測）

**同じ基盤が3つの名前で現れる。** SPF の IP レンジが完全一致することで確認:

```
_spf.activegate-ss.jp   クオリティア Active! gate SS（本家）
mx0N.active-w.net       MXモバイリング Active! world（OEM）
_spf.sbt-mailgate.jp    SBテクノロジー Mail Safe（OEM）
```

シナジーマーケティングも `crmstyle.com`（Synergy!LEAD）と `smp.ne.jp`
（Synergy!）の2系統で、合わせて43件。**別ベンダーとして数えると過大になる。**

辞書に `same_as` のような別名の束ねを持たせること。

---

## 7. テナント実在の確認（DNS の外に出る）

**③を DNS だけで確定させるのは原理的に不可能**である（§2、§4）。残骸と
現役を区別できない。

`https://login.microsoftonline.com/getuserrealm.srf?login=user@<domain>&json=1`
がこれを直接解く。**実測**:

| ドメイン | NameSpaceType | DomainName | FederationBrandName | 読み |
|---|---|---|---|---|
| `microsoft.com` | Managed | microsoft.com | Microsoft | Entra のクラウド認証 |
| `iij.ad.jp` | Managed | iij.ad.jp | IIJ Group | 同上 |
| `nintendo.co.jp` | Federated | nintendo.co.jp | Nintendo Co., Ltd. | **テナントあり。認証を外部 IdP に委任** |
| `outlook.com` | Federated | **live.com** | **Windows Live** | **個人アカウント。組織テナントではない** |
| 存在しないドメイン | Unknown | — | — | どのテナントにも属さない |

**判定**:

```
Managed                                     → ③に数える
Federated かつ DomainName が自ドメイン       → ③に数える（ADFS / Okta 等に委任）
Federated かつ DomainName = live.com        → 数えない（個人アカウント）
Unknown                                     → 数えない
```

**収穫**: `FederationBrandName` がテナントの表示名を返す。M365 調査が指摘した
「MX のトークンが自ドメイン由来でない＝親会社や MSP のテナント配下」を
直接確かめられる。

### 7.1 越えてはならない線

**この端点はドメイン単位で答える。** 架空のアドレス（`user@<domain>`）を
投げてもテナント名まで返る（**実測**）── つまり**アカウントの存在確認を
していない。** だから使える。

**`GetCredentialType` には行かないこと。** 「Entra と個人アカウントの両方で
使われていると選択画面が出る」現象はアカウント単位であり、それを解くには
**実在するアドレスを投げる**必要がある。それは**アカウント列挙**であり、
「個人名を含むメールアドレスを収集・保存しない」という規約に反する。

**選択画面が出るドメインでも、Entra テナントが存在することは変わらない。**
③の判定は壊れない。あの現象は測る対象の外側にある。

### 7.2 バイラルテナントを③から分ける

誰かが会社のアドレスで個人的に登録すると、**会社が何も契約していないのに
影テナントができる。** realm はそれも「テナントあり」と答える。応答に
`IsViral` が立つ場合があるので、**立ったら③から分けること**（今回の実測
4件では立たなかった）。

### 7.3 運営者の判断が要る2点

1. **計測の性格が変わる。** このプロジェクトは「DNS で見える範囲」を観測する
   設計である。HTTP で Microsoft に問い合わせると、**こちらが列挙している
   ドメインの一覧が Microsoft に渡る**（3,400件/月）。SEC に連絡先付きの
   User-Agent を義務づけているのと同じ配慮が要る
2. **取れる情報が目的を超える。** `AuthURL` は「その企業がどの IdP を使って
   いるか」であって、メール認証の準拠状況ではない。**名前に `DNS` を入れて
   範囲を絞っている方針と衝突しうる**

→ **`NameSpaceType` と `FederationBrandName` までは取り、`AuthURL` は
記録しない**（あるいは「外部 IdP あり/なし」の真偽だけにする）ことを推奨する。

**Google に同等の端点は無い。** ③の非対称はここでも効く。

---

## 8. 実装の順序（案）

効き目の大きい順。1〜3 は設計変更を伴わないので先に出せる。

1. **Microsoft の新形式を足す**（§3.1）── 現行のバグ。worklist 31件＋DKIM 約9%
2. **HENNGE を送信前段として足す**（§3.2）── 実測9%。**丸ごと落ちている**
3. **worklist 上位の国内サービスを足す**（§3.3）── 628件のうち大半
4. **層を3つに分ける**（§1.2）── 契約とスキーマの変更
5. **「読めない」を分類する**（§5）── (a)(b)(c)(d) を別々の印にする
6. **別名の束ね**（§6.3）── 二重計上を止める
7. **テナント実在確認**（§7）── 運営者の判断待ち

### 8.1 対照クエリは既にある

Google 調査が「ワイルドカード DNS 対策に、ドメインごとにランダムな
セレクタで対照クエリを打て」と強く勧めているが、**この repo は既にやって
いる**（`DKIM_WILDCARD_SUSPECT`、run 14 で11件）。維持すること。

### 8.2 SPF は再帰展開が要る

**実測**: `dena.com` は `_spf2.dena.com` 配下、`mixi.co.jp` は
`ext01.mixi.co.jp` 配下に Google の include が隠れていた。**トップレベルだけ
見ると取りこぼす。** `redirect=` も追うこと（深さ上限を決める）。

---

## 9. 未確定・推察のまま残っているもの

**実装の根拠にする前に確かめること。**

| 項目 | 状態 |
|---|---|
| `dkim.mail.microsoft` の実在 | SOA を引けなかった。`mx.microsoft` / `mail.microsoft` は**実測で確認済み**だが、DKIM 側は調査2本の報告のみ |
| `*.mailsecure.jp`（worklist 15件） | **同定できず。** NS は `asns[12].customer.ne.jp` |
| `*.sdx.ne.jp`（しまむら） | 同定できず |
| `spf25.jp` の提供者 | TwoFive と**推察**。公式ページで確認できていない |
| 21Vianet（中国版）の MX ホスト名 | SPF include は公式確認済み。MX は**推察** |
| autodiscover CNAME の解約後の残存しやすさ | 公式言及なし。**推察**（上書き圧力があるので `MS=` より残りにくい） |
| ゲートウェイ配下での SPF include と DKIM CNAME の網羅率の大小 | **推察**。実測で検証すべき最重要仮説 |
| 国内サービスの構成比 | 調査の実測サンプル（468件・大型株中心）は**偏っている**。比率は worklist の実頻度を優先すること |

---

## 10. この設計が前提にしている限界

**出す数字に必ず添えること。**

1. **3か月経つまで、残骸と現役を区別できない**（§4.3）
2. **Google は Microsoft と同じ確度では数えられない**（§2）
3. **自社運用（約11%）は製品が分からない**（§5b）
4. **API 連携型は原理的に見えない**（§5a）── 「使っていない」ではない
5. **SPF 平坦化で読めないものがある**（§5c）
6. **①②③は別のものを数えている。** 1つの数字に丸めない
