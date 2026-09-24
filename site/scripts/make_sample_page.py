"""サンプルページを表紙から作る。

表紙（`index.md`）と同じ中身を、`site/src/sample/` の数字で描くページを出す。
**手で複製しない。** 複製すると、表紙を直したときにサンプルだけ古くなり、
「データが入るとこう見える」が嘘になる。しかも画面を開くまで気付けない。

そこで表紙から機械的に作り、**生成物と一致することを検査で縛る**
（`tests/test_compliance.py`）。表紙を直したらこれを流し直す。

    python site/scripts/make_sample_page.py

## 変えているのは3つだけ

  1. 読み先を `data/` から `sample/` に差し替える
  2. 先頭に「サンプルである」ことわりを入れる
  3. front matter の題を差し替える（一覧とタブに出る）

本文には触らない。**触り始めると、そこから乖離が始まる。**
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "site" / "src"

#: サンプルであることのことわり。**題字より上に置く。**
#: 数字を見る前に目に入らないと意味が無い
BANNER = {
    "ja": (
        '<div class="sample-banner" role="note">\n'
        "  <strong>これはサンプルです。</strong>\n"
        "  数字はすべて作り物で、実際に観測したものではありません。\n"
        "  計測が一巡したときに表紙がどう見えるかを確かめるためのページです。\n"
        '  実際の数字は<a href="/">表紙</a>にあります。\n'
        "</div>\n"
    ),
    "en": (
        '<div class="sample-banner" role="note">\n'
        "  <strong>This is a sample.</strong>\n"
        "  Every figure here is made up and none of it was observed.\n"
        "  The page exists to show how the front page reads once a run has completed.\n"
        '  The real figures are on the <a href="/en/">front page</a>.\n'
        "</div>\n"
    ),
}

TITLE = {"ja": "サンプル（作り物の数字）", "en": "Sample (made-up figures)"}

PAGES = {
    # 言語: (元, 出力, 読み先の差し替え)
    "ja": (SRC / "index.md", SRC / "sample.md", ('FileAttachment("data/', 'FileAttachment("sample/')),
    "en": (
        SRC / "en" / "index.md",
        SRC / "en" / "sample.md",
        ('FileAttachment("../data/', 'FileAttachment("../sample/'),
    ),
}

FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n+", re.S)


def render(source: str, lang: str) -> str:
    """表紙の本文からサンプルページを作る。**ここが唯一の変換の定義。**"""
    old, new = PAGES[lang][2]
    if old not in source:
        raise SystemExit(f"{lang}: 読み先 {old!r} が見つからない。表紙の作りが変わった")

    front = ""
    body = source
    m = FRONT_MATTER.match(source)
    if m:
        body = source[m.end():]
        # 題だけ差し替え、残り（フッタの限界の明示など）はそのまま持ち越す。
        # **英語ページはフッタを front matter で持っている**ので落とせない
        kept = [
            ln for ln in m.group(1).splitlines() if not ln.startswith("title:")
        ]
        front = "\n".join([f"title: {TITLE[lang]}", *kept])
    else:
        front = f"title: {TITLE[lang]}"

    return f"---\n{front}\n---\n\n{BANNER[lang]}\n{body.replace(old, new)}"


def main() -> int:
    for lang, (src, out, _) in PAGES.items():
        text = render(src.read_text(encoding="utf-8"), lang)
        changed = not out.is_file() or out.read_text(encoding="utf-8") != text
        out.write_text(text, encoding="utf-8")
        print(f"  {out.relative_to(REPO)} {'書き直した' if changed else '変化なし'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
