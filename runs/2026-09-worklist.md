# 未知 MX ホストの作業リスト 2026-09

辞書に一致しなかった MX ホストを、登録ドメイン単位で頻度順に並べています。
**上から順に手で同定して `configs/fingerprints/` に追記すると推定率が上がります。**

連続 3 か月以上未知のままのものには印を付けています。
同定できないと判断したものは
`configs/worklist/unidentified_hosts.yaml` に理由を書いて作業リストから外してください。
**「調べたが分からなかった」は結論であって、無かったことにはしません。**

## 手を付けるもの（20 件）

| 登録ドメイン | 件数 | 連続月 | 例 |
|---|---|---|---|
| `ocn.ad.jp` | 138 | 1 | `mwpremgw2.ocn.ad.jp`, `mwpremgw1.ocn.ad.jp`, `vcgw2.ocn.ad.jp` |
| `fireeyecloud.com` | 74 | 1 | `alt3.ap.email.fireeyecloud.com`, `alt2.ap.email.fireeyecloud.com`, `primary.ap.email.fireeyecloud.com` |
| `alpha-prm.jp` | 57 | 1 | `mxi.alpha-prm.jp` |
| `messagelabs.com` | 57 | 1 | `cluster5a.us.messagelabs.com`, `cluster5.us.messagelabs.com`, `cluster1a.us.messagelabs.com` |
| `kagoya.net` | 48 | 1 | `dmail.kagoya.net`, `mgws317.kagoya.net`, `mgws299.kagoya.net` |
| `secure.ne.jp` | 46 | 1 | `ham1005.secure.ne.jp`, `vlmx-air.secure.ne.jp`, `ham1010.secure.ne.jp` |
| `outlook.com` | 31 | 1 | `ms29696915.msv1.invalid.outlook.com`, `faltec-co-jp.mail.eo.outlook.com`, `hoshizaki-co-jp.mail.eo.outlook.com` |
| `airnet.ne.jp` | 30 | 1 | `mxin2.airnet.ne.jp`, `mxin1.airnet.ne.jp` |
| `worksmobile.com` | 17 | 1 | `jp1-aspmx1.worksmobile.com`, `jp1-aspmx2.worksmobile.com` |
| `alpha-mail.net` | 16 | 1 | `ampub03.alpha-mail.net`, `ampub04.alpha-mail.net`, `ampub01.alpha-mail.net` |
| `active-w.net` | 15 | 1 | `mx02.active-w.net`, `mx03.active-w.net`, `mx01.active-w.net` |
| `mailsecure.jp` | 15 | 1 | `v2301-244.mailsecure.jp`, `v1700-176.mailsecure.jp`, `v1300-184.mailsecure.jp` |
| `digitalartscloud.com` | 12 | 1 | `mail.system.digitalartscloud.com` |
| `larksuite.com` | 12 | 1 | `mx2.larksuite.com`, `mx3.larksuite.com`, `mx1.larksuite.com` |
| `fortimail.com` | 11 | 1 | `gw4022.fortimail.com`, `gw3018.fortimail.com`, `gw199127.fortimail.com` |
| `heteml.jp` | 10 | 1 | `mx-proxy502.heteml.jp`, `mx-proxy501.heteml.jp` |
| `nospamcloud.com` | 10 | 1 | `mxjp2.nospamcloud.com`, `mxjp1.nospamcloud.com` |
| `oneoffice.jp` | 10 | 1 | `mailgw3.oneoffice.jp`, `mailgw2.oneoffice.jp`, `mailgw.oneoffice.jp` |
| `sharedmail.jp` | 10 | 1 | `filter1.mail.sharedmail.jp`, `mx1.sharedmail.jp`, `filter2.mail.sharedmail.jp` |
| `lolipop.jp` | 9 | 1 | `mx01.lolipop.jp` |

- P6 の未知ホスト一覧が上限 20 件に達している。**一覧に無いホストが残っている**（頻度の低いものが切れている）

## 追記のしかた

`configs/fingerprints/*.yaml` に規則を足します。コンソールの
「辞書メンテナンス」画面（画面5）からも追加できます。追加した規則は
**次回の P6 から効きます。過去の月は作り直しません**
（訂正の方針と同じ）。
