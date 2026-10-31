# 未知 MX ホストの作業リスト 2026-10

辞書に一致しなかった MX ホストを、登録ドメイン単位で頻度順に並べています。
**上から順に手で同定して `configs/fingerprints/` に追記すると推定率が上がります。**

連続 3 か月以上未知のままのものには印を付けています。
同定できないと判断したものは
`configs/worklist/unidentified_hosts.yaml` に理由を書いて作業リストから外してください。
**「調べたが分からなかった」は結論であって、無かったことにはしません。**

## 手を付けるもの（41 件）

| 登録ドメイン | 件数 | 連続月 | 例 |
|---|---|---|---|
| `arsmtp.com` | 10 | 1 | `koss.com.2.arsmtp.com`, `koss.com.1.arsmtp.com`, `vcel-com.mx1.arsmtp.com` |
| `gpphosted.com` | 4 | 1 | `mxb-00588301.gslb.gpphosted.com`, `mxa-00588301.gslb.gpphosted.com`, `mxa-009e9301.gslb.gpphosted.com` |
| `netease.com` | 4 | 1 | `163mx02.mxmail.netease.com`, `163mx01.mxmail.netease.com`, `163mx03.mxmail.netease.com` |
| `healthsouth.com` | 3 | 1 | `hpp2.healthsouth.com`, `hpp3.healthsouth.com`, `hpp4.healthsouth.com` |
| `mailhostbox.com` | 3 | 1 | `us3.mx3.mailhostbox.com`, `us3.mx2.mailhostbox.com`, `us3.mx1.mailhostbox.com` |
| `mx.cloudflare.net` | 3 | 1 | `route3.mx.cloudflare.net`, `route2.mx.cloudflare.net`, `route1.mx.cloudflare.net` |
| `scanscope.net` | 3 | 1 | `mail3.us.scanscope.net`, `mail2.us.scanscope.net`, `mail1.us.scanscope.net` |
| `spamhero.net` | 3 | 1 | `rciis-com.p30.spamhero.net`, `rciis-com.p20.spamhero.net`, `rciis-com.p40.spamhero.net` |
| `263.net` | 2 | 1 | `mx.263.net`, `mxw.263.net` |
| `bell.ca` | 2 | 1 | `mx-dor.bell.ca`, `mx-wyn.bell.ca` |
| `biz-email.net` | 2 | 1 | `mail.biz-email.net`, `cnc.biz-email.net` |
| `chinaemail.cn` | 2 | 1 | `mxabjm.chinaemail.cn`, `mxbjm.chinaemail.cn` |
| `citi.com` | 2 | 1 | `mx-b.mail.citi.com`, `mx-a.mail.citi.com` |
| `emailservice.io` | 2 | 1 | `oxfordsquarecapital-com.us.emailservice.io`, `shpreit-com.inbound.emailservice.io` |
| `heritagebanknw.com` | 2 | 1 | `mx1.heritagebanknw.com`, `mx2.heritagebanknw.com` |
| `hknet.com` | 2 | 1 | `ms2.hknet.com`, `riverstone.hknet.com` |
| `mailgun.org` | 2 | 1 | `mxb.mailgun.org`, `mxa.mailgun.org` |
| `pwrd.com` | 2 | 1 | `mx02.pwrd.com`, `mx01.pwrd.com` |
| `reg365.net` | 2 | 1 | `mx2.reg365.net`, `mx1.reg365.net` |
| `164.4` | 1 | 1 | `205.220.164.4` |
| `176.4` | 1 | 1 | `205.220.176.4` |
| `237.35` | 1 | 1 | `50.170.237.35` |
| `aliyun.com` | 1 | 1 | `mx01.dm.aliyun.com` |
| `amazonses.com` | 1 | 1 | `feedback-smtp.us-east-1.amazonses.com` |
| `bezeqint.net` | 1 | 1 | `mx.bezeqint.net` |
| `cn4e.com` | 1 | 1 | `smx29.cn4e.com` |
| `corpsmtp.cn` | 1 | 1 | `v-apparel-com.corpsmtp.cn` |
| `corpsmtp.net` | 1 | 1 | `v-apparel-com.corpsmtp.net` |
| `dataplugs.com` | 1 | 1 | `web1012.dataplugs.com` |
| `emailservice.cc` | 1 | 1 | `shpreit-com.inbound.emailservice.cc` |
| `emailservice.co` | 1 | 1 | `shpreit-com.inbound.emailservice.co` |
| `ko.com` | 1 | 1 | `ns1.ko.com` |
| `mailhop.org` | 1 | 1 | `a916673.mx.mailhop.org` |
| `mailroute.net` | 1 | 1 | `mail.mailroute.net` |
| `mbox.net` | 1 | 1 | `mx.ct.mbox.net` |
| `powertech.no` | 1 | 1 | `mx.powertech.no` |
| `rapidsite.net` | 1 | 1 | `mail-fwd.mx.g19.rapidsite.net` |
| `serverdata.net` | 1 | 1 | `east.smtp.mx.o365.serverdata.net` |
| `spamhero.com` | 1 | 1 | `rciis-com.p10.spamhero.com` |
| `teco.com.ar` | 1 | 1 | `buepac-mx-08.teco.com.ar` |
| `usa.net` | 1 | 1 | `mx.usa.net` |

## 追記のしかた

`configs/fingerprints/*.yaml` に規則を足します。コンソールの
「辞書メンテナンス」画面（画面5）からも追加できます。追加した規則は
**次回の P6 から効きます。過去の月は作り直しません**
（訂正の方針と同じ）。
