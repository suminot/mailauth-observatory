# 未知 MX ホストの作業リスト 2026-09

辞書に一致しなかった MX ホストを、登録ドメイン単位で頻度順に並べています。
**上から順に手で同定して `configs/fingerprints/` に追記すると推定率が上がります。**

連続 3 か月以上未知のままのものには印を付けています。
同定できないと判断したものは
`configs/worklist/unidentified_hosts.yaml` に理由を書いて作業リストから外してください。
**「調べたが分からなかった」は結論であって、無かったことにはしません。**

## 手を付けるもの（8 件）

| 登録ドメイン | 件数 | 連続月 | 例 |
|---|---|---|---|
| `avnet.com` | 6 | 1 | `smtpemea02.avnet.com`, `smtp03.avnet.com`, `smtp01.avnet.com` |
| `americanexpress.com.mx` | 2 | 1 | `servicetest.americanexpress.com.mx`, `service.americanexpress.com.mx` |
| `avery.com` | 2 | 1 | `mail10.avery.com`, `mail11.avery.com` |
| `cclind.com` | 2 | 1 | `mail2.cclind.com`, `mail1.cclind.com` |
| `gpphosted.com` | 2 | 1 | `mxa-00517304.gslb.gpphosted.com`, `mxb-00517304.gslb.gpphosted.com` |
| `aig.com.br` | 1 | 1 | `mail3.aig.com.br` |
| `digital--analog.com` | 1 | 1 | `digital--analog.com` |
| `unumemarketing.com` | 1 | 1 | `unumemarketing.com` |

## 追記のしかた

`configs/fingerprints/*.yaml` に規則を足します。コンソールの
「辞書メンテナンス」画面（画面5）からも追加できます。追加した規則は
**次回の P6 から効きます。過去の月は作り直しません**
（訂正の方針と同じ）。
