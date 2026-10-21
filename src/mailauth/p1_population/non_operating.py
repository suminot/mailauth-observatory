"""事業会社でないもの（SPAC・ファンド・ETF）を見分ける。

**運営者の判断（2026-10-21）: 母集団から外し、外した会社は名前と理由を示す。**

これらは社員もメールサーバーも持たず、運用会社やスポンサーのものを使う。
母集団に入れたままだと、

  - 起点ドメインが見つからず「起点なし」として率を下げる（米国で約 770 社）
  - 見つかっても運用会社のドメイン（`nuveen.com`）に数十社が寄り、
    **1つの設定が数十の「企業」として数えられる**

判定は SEC 自身の区分で行う。**社名の語だけでは決めない**（「Trust」は
銀行にも、「Acquisition」は買収を終えた事業会社にも付く）。

  SPAC                 業種コード 6770（白地小切手会社）、または名称が
                       「〇〇 Acquisition Corp / Company」
                       （**SEC が買収予定先の業種コードを付けた SPAC が 23 社
                       あった**（2026-10-21 の全数）。社名で決める唯一の例外）
  商品・暗号資産の信託  業種コード 6221、かつ社名が ETF / Trust / Fund
                       （6221 には AI Financial Corp など事業会社も入っている）
  ロイヤルティ信託・    業種コード 6189（資産担保証券）すべてと、6792（石油ロイヤルティ）
  仕組み債の信託        のうち名称に Trust があるもの。受託銀行が管理するだけの器で、
                       Texas Pacific Land など 6792 の事業会社は残す
  登録投資会社          投資会社の届出（N-CSR・NPORT-P・N-2 など）を出していて、
                       10-K / 10-Q / 20-F / 40-F を出していない
                       （BDC は 10-K を出す会社なので残る）
"""

from __future__ import annotations

import re
from collections.abc import Iterable

SPAC = "spac"
COMMODITY_TRUST = "commodity_trust"
REGISTERED_FUND = "registered_fund"
PASSIVE_TRUST = "passive_trust"

#: 区分の表示名。公開サイトにもこの名前で出す
LABELS_JA = {
    SPAC: "SPAC（買収目的の会社）",
    COMMODITY_TRUST: "商品・暗号資産の上場信託",
    REGISTERED_FUND: "投資信託・ETF・クローズドエンド型ファンド",
    PASSIVE_TRUST: "ロイヤルティ信託・仕組み債の信託",
}
LABELS_EN = {
    SPAC: "SPAC (blank-check company)",
    COMMODITY_TRUST: "Commodity or crypto trust",
    REGISTERED_FUND: "Fund, ETF or closed-end fund",
    PASSIVE_TRUST: "Royalty or structured trust",
}

SPAC_SIC = "6770"
COMMODITY_SIC = "6221"
ASSET_BACKED_SIC = "6189"
ROYALTY_SIC = "6792"

#: 1940年投資会社法の登録投資会社だけが出す届出
FUND_FORMS = frozenset(
    {"N-CSR", "N-CSRS", "NPORT-P", "N-CEN", "N-2", "N-2/A", "N-1A", "485BPOS", "N-30D", "N-Q"}
)

#: 事業会社が出す定期報告。これを出していれば登録投資会社とは見なさない
OPERATING_FORMS = frozenset({"10-K", "10-K/A", "10-Q", "20-F", "40-F"})

_FUND_NAME = re.compile(r"\b(etf|etp|trust|fund)\b", re.I)
_TRUST_NAME = re.compile(r"\btrust\b", re.I)

#: SPAC の名付け方。「Acquisition Holdings」は事業会社にもあるので含めない
_SPAC_NAME = re.compile(r"\bacquisition\s+(?:corp(?:oration)?|co|company)\b", re.I)


def classify(sic: str | None, forms: Iterable[str] | None, name: str | None) -> str | None:
    """事業会社でなければその区分を、事業会社なら None を返す。"""
    code = sic.strip() if isinstance(sic, str) else ""
    if code == SPAC_SIC or _SPAC_NAME.search(name if isinstance(name, str) else ""):
        return SPAC
    if code == COMMODITY_SIC and _FUND_NAME.search(name or ""):
        return COMMODITY_TRUST
    if code == ASSET_BACKED_SIC or (code == ROYALTY_SIC and _TRUST_NAME.search(name or "")):
        return PASSIVE_TRUST
    filed = set(forms or ())
    if filed & FUND_FORMS and not filed & OPERATING_FORMS:
        return REGISTERED_FUND
    return None
