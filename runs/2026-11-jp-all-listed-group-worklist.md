# 未知 MX ホストの作業リスト 2026-11

辞書に一致しなかった MX ホストを、登録ドメイン単位で頻度順に並べています。
**上から順に手で同定して `configs/fingerprints/` に追記すると推定率が上がります。**

連続 3 か月以上未知のままのものには印を付けています。
同定できないと判断したものは
`configs/worklist/unidentified_hosts.yaml` に理由を書いて作業リストから外してください。
**「調べたが分からなかった」は結論であって、無かったことにはしません。**

## 手を付けるもの（17 件）

| 登録ドメイン | 件数 | 連続月 | 例 |
|---|---|---|---|
| `index.or.jp` | 10 | 1 | `nsniigw02.index.or.jp`, `nsniigw04.index.or.jp`, `nsniigw01.index.or.jp` |
| `uniqlo.co.jp` | 4 | 1 | `mail2.uniqlo.co.jp`, `mail4.uniqlo.co.jp`, `mail1.uniqlo.co.jp` |
| `mxthunder.net` | 3 | 1 | `tsukuba-milk-co-jp.p30.mxthunder.net`, `tsukuba-milk-co-jp.p40.mxthunder.net`, `tsukuba-milk-co-jp.p20.mxthunder.net` |
| `amazonses.com` | 2 | 1 | `feedback-smtp.ap-northeast-1.amazonses.com` |
| `blks.jp` | 2 | 1 | `control-mail-mta01.blks.jp`, `control-mail-mta02.blks.jp` |
| `s3od.jp` | 2 | 1 | `mx4.s3od.jp`, `mx3.s3od.jp` |
| `secu-dity.jp` | 2 | 1 | `bc.secu-dity.jp` |
| `cymail.sakura.ne.jp` | 1 | 1 | `cymail.sakura.ne.jp` |
| `domainserver.ne.jp` | 1 | 1 | `ds082-304.domainserver.ne.jp` |
| `fastretailing.com` | 1 | 1 | `mail.aws.fastretailing.com` |
| `hoshizaki-tokai.sakura.ne.jp` | 1 | 1 | `hoshizaki-tokai.sakura.ne.jp` |
| `hostmasteris.me` | 1 | 1 | `adpub.hostmasteris.me` |
| `iij.ad.jp` | 1 | 1 | `bh0.iij.ad.jp` |
| `mxthunder.com` | 1 | 1 | `tsukuba-milk-co-jp.p10.mxthunder.com` |
| `shared-server.net` | 1 | 1 | `mx.red.shared-server.net` |
| `www3578.sakura.ne.jp` | 1 | 1 | `www3578.sakura.ne.jp` |
| `yamaso-web.co.jp` | 1 | 1 | `columba.yamaso-web.co.jp` |

## 調査済み・同定できなかったもの（1 件）

**件数は残します。** 「調べたが分からなかった」を無かったことにしないためです。

| 登録ドメイン | 件数 | 理由 | 調査日 |
|---|---|---|---|
| `biglobe.ne.jp` | 2 | 公開情報から同定できない | 2026-11-01 |

## 追記のしかた

`configs/fingerprints/*.yaml` に規則を足します。コンソールの
「辞書メンテナンス」画面（画面5）からも追加できます。追加した規則は
**次回の P6 から効きます。過去の月は作り直しません**
（訂正の方針と同じ）。
