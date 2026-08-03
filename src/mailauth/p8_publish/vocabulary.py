"""表現上の規約（DESIGN.md P8「表現上の規約」）。

**これは法務要件でもあるため、実装時に妥協しない。** 実在する企業の設定状態を
名指しで公開する以上、表現が断定的になるほど名誉毀損のリスクが上がる。

守ること
  - 「危険」「脆弱」等の断定を避け、標準準拠の事実記述に限定する
  - 総合順位や A〜F グレードを付けない。代わりに達成した標準のチェックリストを出す
  - 「本サイトは標準準拠の計測であり、総合的セキュリティ評価ではない」を常時表示する

語彙の検査はコメントで宣言するだけでは守られない。ここを CI から呼んで落とす。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: 使ってはいけない断定的な語彙。読み手に「この企業は危険だ」と受け取らせる表現。
#: 計測しているのは標準への準拠状況であって、その企業の安全性ではない。
FORBIDDEN_TERMS: dict[str, str] = {
    "危険": "「業界標準に照らして REQUIRED を満たさない」のような事実記述に置き換える",
    "脆弱": "観測した事実（p=none である等）と、その標準上の意味だけを書く",
    "無防備": "断定を避ける。観測できた範囲を明示する",
    "落第": "順位付け・合否の断定はしない",
    "ワースト": "順位付けをしない",
    "ランキング": "総合順位を付けない。達成した標準のチェックリストを出す",
    "最下位": "順位付けをしない",
    "劣悪": "断定を避ける",
    "ずぼら": "断定を避ける",
    "怠慢": "主観的な評価を書かない",
    "放置企業": "ドメインの分類語（neglected）を企業の評価に転用しない",
}

#: A〜F のグレード表記。総合評価に見えるので使わない
GRADE_PATTERN = re.compile(r"(?<![A-Za-z0-9])[A-F][+\-]?\s*(?:評価|ランク|グレード|判定)")

#: 常時表示しなければならない限界の明示
DISCLAIMER = "本サイトは標準準拠の計測であり、総合的セキュリティ評価ではない"

#: 3段階の配色。赤の面積を最小化するため「未対応」はグレー寄りの赤にする
COLOR_ROLES = {
    "pass": "合格",
    "attention": "要改善",
    "absent": "未対応",
}


@dataclass
class Violation:
    term: str
    advice: str
    excerpt: str


def check(text: str) -> list[Violation]:
    """禁止語彙とグレード表記を探す。

    見つかった語の周辺も返す。どこを直せばよいか分からない指摘は直されない。
    """
    out: list[Violation] = []
    for term, advice in FORBIDDEN_TERMS.items():
        for match in re.finditer(re.escape(term), text):
            start = max(match.start() - 30, 0)
            out.append(
                Violation(
                    term=term,
                    advice=advice,
                    excerpt=text[start : match.end() + 30].replace("\n", " "),
                )
            )
    for match in GRADE_PATTERN.finditer(text):
        start = max(match.start() - 30, 0)
        out.append(
            Violation(
                term=match.group(0),
                advice="総合順位・A〜F グレードは付けない（DESIGN.md P8）",
                excerpt=text[start : match.end() + 30].replace("\n", " "),
            )
        )
    return out


def has_disclaimer(text: str) -> bool:
    """限界の明示が含まれているか。

    表記ゆれを許すため、句読点と空白を落として比較する。
    """
    normalized = re.sub(r"[\s、。,.]", "", text)
    return re.sub(r"[\s、。,.]", "", DISCLAIMER) in normalized


def describe_policy(policy: str | None, *, has_rua: bool = True) -> str:
    """DMARC ポリシーを、標準準拠の事実記述で説明する。

    「危険」ではなく「p=none のため spoofing 抑止効果は限定的」と書く。
    """
    if policy == "reject":
        text = "p=reject。認証に失敗したメールの拒否を要求している"
    elif policy == "quarantine":
        text = "p=quarantine。認証に失敗したメールの隔離を要求している"
    elif policy == "none":
        text = "p=none のため spoofing 抑止効果は限定的。観測のみの段階にある"
    elif policy is None:
        text = "DMARC レコードを観測できなかった。レコードが無いこととは別"
    else:
        text = f"p={policy}"

    if policy in ("reject", "quarantine") and not has_rua:
        text += "。ただし rua が無く、何が拒否されているかを運用者が確認できない"
    return text
