"""英字社名から .co.jp を推測し、そのサイトに社名が載っているかで確かめる（BACKLOG 15）。

国内の起点なしを埋める経路。gBizINFO と Wikidata のあとに、**まだ空欄の企業
だけ**に使う。2026-09 の国内計測では、測ったドメインが1つも無い上場企業が
609 社あり、うち 453 社は EDINET に英字社名があった。

なぜ .co.jp か
  - .co.jp は**1つの組織が1つしか持てない**（JPRS の登録規則）
  - 上場企業の大半は英字社名の語をそのまま .co.jp にしている
    （MURAKI CORPORATION → muraki.co.jp）
  - **.co.jp に無ければ .jp・.com も見る**（2026-11-02）。ただしこの2つは
    1組織1つの決まりが無いので、社名の長さに関わらず、タイトルに商号の形
    （「株式会社◯◯」など）で載っていることを求める

なぜ「サイトに社名が載っているか」で確かめるか
  - **推測は推測でしかない。** 短い語ほど他社が持っている（2026-10-31 の
    試験で、area.co.jp は Area,Inc.、plus.co.jp は PLUS CORP. のものだった。
    探していたのはエリアクエストとプラスアルファ・コンサルティング）
  - WHOIS の登録組織名でも確かめられるが、WhoisXML API の規約は取得した
    データとその派生物を第三者に渡すことを禁じている。このリポジトリは
    公開なので使わない。**その会社自身が公開しているページ**で確かめる

守っていること
  - 確かめる語は**日本語の商号**（株式会社・ホールディングス等を除いた
    部分、3文字以上）。英字の語は他社のサイトにも出やすい
  - 1社につきページは推測が DNS に実在したものだけ。各ドメインで最初に
    読めた入口（https://www. → https:// → http://www. → http:// の順）を
    1回だけ読む。連絡先つきの User-Agent を付ける（SEC と同じ作法）。
    全体で1秒に2本まで
  - 試験（2026-10-31、30社）：WHOIS で確かめた 12 社と一致し、他社の
    ドメインを採ったものは無かった。WHOIS より多く拾った2社（社名変更・
    事業子会社）も正しかった
  - **社名の芯が短い（4文字以下）ときは、ページ全体ではなくタイトルで
    確かめる。** 2026-11 の国内計測で埋めた 177 社を見直したら、誤りは
    すべて芯の短い会社だった。musashi.co.jp（タイトルは武蔵精密工業）、
    jrc.co.jp（日本無線）、tomita.co.jp（富田興業）、bcc.co.jp（株式会社
    ビーシーシー）、kozo.co.jp（構造システム）、uls.co.jp（株式会社ウルス）
    など。短い語は他社のページのどこかに載ってしまう。タイトル（og:title・
    og:site_name を含む）に「株式会社◯◯」「◯◯株式会社」「◯◯ホールディ
    ングス」などの形で載っていることを求める。同じ見直しで、正しかった
    短い社名の会社の約1割がこれで落ちる（タイトルが商品名だけのもの）
"""

from __future__ import annotations

import codecs
import html
import re
import threading
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit

#: 推測に使わない語。法人格と、どの会社にも付く語
_STOP = {
    "CO",
    "LTD",
    "LIMITED",
    "INC",
    "CORP",
    "CORPORATION",
    "COMPANY",
    "KABUSHIKI",
    "KAISHA",
    "KK",
    "K",
    "HOLDINGS",
    "HOLDING",
    "GROUP",
    "THE",
    "AND",
    "OF",
}

#: 日本語の商号から外す語。残った部分がページに載っているかを見る
_JA_DROP = ("株式会社", "ホールディングス", "グループ", "（株）", "(株)", "・", " ", "　")

#: 社名の芯がこれより短いと、他社のページにも偶然載る
MIN_CORE_CHARS = 3

#: 社名の芯がこの長さ以下なら、ページ全体ではなくタイトルで確かめる
SHORT_CORE_CHARS = 4

#: タイトルで社名と認める形。{c} が社名の芯
_TITLE_FORMS = ("株式会社{c}", "{c}株式会社", "(株){c}", "{c}ホールディングス", "{c}グループ")

#: 1社あたりに試す推測の数の上限
MAX_GUESSES = 4

#: 読むページの大きさ
MAX_BYTES = 2_000_000


def name_tokens(name_en: str | None) -> list[str]:
    """英字社名から、法人格などを除いた語を小文字で返す。"""
    words = re.sub(r"[^A-Z0-9 ]", " ", (name_en or "").upper()).split()
    return [w.lower() for w in words if w not in _STOP]


def _group_forms(name_en: str | None, words: list[str]) -> list[str]:
    """持株会社・フィナンシャルグループの決まった形（`-hd` `-holdings` `-fg`）。

    上場の持株会社は `<名>-hd.co.jp` / `<名>-holdings.co.jp`、地銀の
    フィナンシャルグループは `<名>-fg.co.jp` を使うことが多い（例:
    tokyu-fudosan-hd.co.jp、jfe-holdings.co.jp、mebuki-fg.co.jp、
    kyushu-fg.co.jp）。`HOLDINGS` / `GROUP` は `_STOP` で落としているので、
    つなげた形だけでは届かない。確かめはほかの推測と同じくページで行う。
    """
    raw = set(re.sub(r"[^A-Z0-9 ]", " ", (name_en or "").upper()).split())
    forms: list[str] = []
    if raw & {"HOLDINGS", "HOLDING"}:
        joined, hyphen = "".join(words), "-".join(words)
        forms += [f"{hyphen}-hd", f"{joined}hd", f"{hyphen}-holdings", f"{joined}holdings"]
    if "FINANCIAL" in raw and "GROUP" in raw:
        base = [w for w in words if w != "financial"]
        if base:
            forms += [f"{'-'.join(base)}-fg", f"{''.join(base)}fg"]
    return forms


def guesses(name_en: str | None) -> list[str]:
    """推測する .co.jp の候補。**つなげた形 → ハイフン → 先頭の語** の順。

    先頭の語だけの形は他社のものであることが多いが、候補には入れる
    （確かめはページで行う）。3文字未満の語は使わない。

    持株会社・フィナンシャルグループは、その決まった形（`_group_forms`）を
    ハイフンの形の次に試す。この分は `MAX_GUESSES` の外に数える。
    """
    words = name_tokens(name_en)
    if not words:
        return []
    forms = ["".join(words), "-".join(words), words[0]]
    if len(words) >= 2:
        forms.append(words[0] + words[1])
    base = list(dict.fromkeys(f"{f}.co.jp" for f in forms if len(f) >= 3))[:MAX_GUESSES]
    group = [f"{f}.co.jp" for f in _group_forms(name_en, words) if len(f) >= 3]
    head, tail = base[:2], base[2:]
    return list(dict.fromkeys(head + group + tail))


_META_REFRESH = re.compile(
    r"<meta[^>]+http-equiv=[\"']?refresh[\"']?[^>]*content=[\"']\s*\d*\s*;?\s*url\s*=\s*"
    r"[\"']?([^\"'>\s]+)",
    re.I,
)


def meta_refresh_target(page: str, base_url: str, domain: str) -> str | None:
    """`<meta http-equiv="refresh">` の飛び先。**同じドメインの中**のときだけ返す。

    別のドメインへ飛ぶもの（駐車ページ・売り物・グループ会社）は追わない。
    そこに社名があっても、このドメインがその会社のものとは言えない。
    """
    m = _META_REFRESH.search(page or "")
    if not m:
        return None
    target = urljoin(base_url, html.unescape(m.group(1)))
    parts = urlsplit(target)
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme not in ("http", "https"):
        return None
    if host != domain and not host.endswith("." + domain):
        return None
    if target.rstrip("/") == base_url.rstrip("/"):
        return None
    return target


#: フレームで組んだトップページ（`<frameset><frame src="main.html">`）
_FRAME_SRC = re.compile(r"<frame\b[^>]*\bsrc=[\"']?([^\"'>\s]+)", re.I)

#: スクリプトだけで飛ばすトップページ（`location.href = "/jp/"` など）
_JS_LOCATION = re.compile(
    r"(?:window\.|document\.|top\.)?location(?:\.href)?\s*=\s*[\"']([^\"']+)[\"']"
    r"|location\.replace\(\s*[\"']([^\"']+)[\"']",
    re.I,
)


def _same_domain(target: str, base_url: str, domain: str) -> str | None:
    parts = urlsplit(target)
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme not in ("http", "https"):
        return None
    if host != domain and not host.endswith("." + domain):
        return None
    if target.rstrip("/") == base_url.rstrip("/"):
        return None
    return target


def entry_redirect_target(page: str, base_url: str, domain: str) -> str | None:
    """入口から飛ぶ先。meta refresh → フレーム → スクリプトの順に見る。

    **同じドメインの中**のときだけ返す（`meta_refresh_target` と同じ理由）。
    フレームとスクリプトは、入口のページで社名が確かめられなかったときだけ
    使う（呼ぶ側）。スクリプトの `location.href = ...` は普通のページにも
    書かれていて、それを先に追うと正しいページを捨てることになる。
    """
    target = meta_refresh_target(page, base_url, domain)
    if target is not None:
        return target
    text = page or ""
    m = _FRAME_SRC.search(text)
    if m:
        found = _same_domain(urljoin(base_url, html.unescape(m.group(1))), base_url, domain)
        if found:
            return found
    m = _JS_LOCATION.search(text)
    if m:
        raw = m.group(1) or m.group(2)
        return _same_domain(urljoin(base_url, html.unescape(raw)), base_url, domain)
    return None


#: .co.jp に無いときに見る TLD。**順に見る**
OTHER_TLDS = ("jp", "com")

#: 確かめられなかった理由の、分かったことの多さ
_OUTCOME_RANK = {"none": 0, "unreachable": 1, "not_on_page": 2}


def other_tld_guesses(candidates: list[str]) -> list[str]:
    """.co.jp の推測を .jp・.com に置き換えた形。**推測の順 → TLD の順**。"""
    stems = [c[: -len(".co.jp")] for c in candidates if c.endswith(".co.jp")]
    return [f"{stem}.{tld}" for tld in OTHER_TLDS for stem in stems]


# -- 読み（カナ）からの推測 ------------------------------------------------
#
# 英字社名が無い会社（2026-11 の国内で 606 社中 166 社）にも、EDINET コード
# リストには「提出者名（ヨミ）」がある。和語の社名は、読みをヘボン式で
# つなげた形をドメインにしていることが多い（タイヘイデンギョウ →
# taiheidengyo、ムラキ → muraki）。外来語の社名（〜システム）は読みから
# 綴りに戻せないので当たらないが、確かめはページで行うので害は無い。

_KANA_DIGRAPHS = {
    "キャ": "kya", "キュ": "kyu", "キョ": "kyo", "シャ": "sha", "シュ": "shu",
    "ショ": "sho", "シェ": "she", "チャ": "cha", "チュ": "chu", "チョ": "cho",
    "チェ": "che", "ニャ": "nya", "ニュ": "nyu", "ニョ": "nyo", "ヒャ": "hya",
    "ヒュ": "hyu", "ヒョ": "hyo", "ミャ": "mya", "ミュ": "myu", "ミョ": "myo",
    "リャ": "rya", "リュ": "ryu", "リョ": "ryo", "ギャ": "gya", "ギュ": "gyu",
    "ギョ": "gyo", "ジャ": "ja", "ジュ": "ju", "ジョ": "jo", "ジェ": "je",
    "ビャ": "bya", "ビュ": "byu", "ビョ": "byo", "ピャ": "pya", "ピュ": "pyu",
    "ピョ": "pyo", "ティ": "ti", "ディ": "di", "デュ": "dyu", "ファ": "fa",
    "フィ": "fi", "フェ": "fe", "フォ": "fo", "ウィ": "wi", "ウェ": "we",
    "ウォ": "wo", "ヴァ": "va", "ヴィ": "vi", "ヴェ": "ve", "ヴォ": "vo",
    "ツァ": "tsa", "トゥ": "tu", "ドゥ": "du",
}
_KANA = dict(
    zip(
        "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモ"
        "ヤユヨラリルレロワヲガギグゲゴザジズゼゾダヂヅデドバビブベボパピプペポヴ"
        "ァィゥェォャュョヮ",
        "a i u e o ka ki ku ke ko sa shi su se so ta chi tsu te to na ni nu ne no "
        "ha hi fu he ho ma mi mu me mo ya yu yo ra ri ru re ro wa o ga gi gu ge go "
        "za ji zu ze zo da ji zu de do ba bi bu be bo pa pi pu pe po vu "
        "a i u e o ya yu yo wa".split(),
        strict=True,
    )
)
#: 読みの中で、社名の語ではないもの
_KANA_DROP = ("カブシキガイシャ", "カブシキカイシャ", "ホールディングス", "グループ")


def kana_to_romaji(kana: str | None) -> str:
    """カナ（ひらがなも可）をヘボン式でつなげる。読めない文字は落とす。

    長音（ー）は落とす。促音（ッ）は次の子音を重ねる（ッチ → tchi）。
    """
    text = unicodedata.normalize("NFKC", kana or "")
    text = "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in text)
    for word in _KANA_DROP:
        text = text.replace(word, "")
    out: list[str] = []
    double = False
    i = 0
    while i < len(text):
        pair, ch = text[i : i + 2], text[i]
        if pair in _KANA_DIGRAPHS:
            roma, i = _KANA_DIGRAPHS[pair], i + 2
        elif ch in _KANA:
            roma, i = _KANA[ch], i + 1
        elif ch == "ン":
            out.append("n")
            i += 1
            continue
        elif ch == "ッ":
            double, i = True, i + 1
            continue
        else:
            i += 1  # ー・中黒・空白・英数字以外
            continue
        if double:
            roma = ("t" if roma.startswith("ch") else roma[0]) + roma
            double = False
        out.append(roma)
    return "".join(out)


def kana_guesses(kana: str | None) -> list[str]:
    """読みから推測する .co.jp。**長音を詰めた形 → 詰めない形** の順。

    ドメインでは「ou」「uu」を詰めることが多い（dengyou → dengyo、
    kyouto → kyoto）。3文字未満は使わない。
    """
    full = kana_to_romaji(kana)
    if len(full) < 3:
        return []
    short = full.replace("ou", "o").replace("uu", "u").replace("oo", "o")
    return list(dict.fromkeys(f"{f}.co.jp" for f in (short, full) if len(f) >= 3))


#: 証明書の組織名で確かめるときに、これより短い社名は使わない（偶然の一致）
MIN_ORG_CHARS = 4


def org_matches(org: str | None, name_en: str | None) -> bool:
    """証明書の組織名と英字社名が、法人格などを除いて**完全に**一致するか。

    部分一致は採らない（「Yamato」は「Yamato Transport」の証明書にも
    「Yamato Holdings」の証明書にも当たる）。
    """
    a = "".join(name_tokens(org))
    b = "".join(name_tokens(name_en))
    return len(b) >= MIN_ORG_CHARS and a == b


def name_core(name_ja: str | None) -> str:
    """日本語の商号から、ページで探す芯を取り出す（NFKC 済み）。"""
    text = unicodedata.normalize("NFKC", name_ja or "")
    for word in _JA_DROP:
        text = text.replace(unicodedata.normalize("NFKC", word), "")
    return text


#: `<meta charset=...>` / `<meta http-equiv="Content-Type" content="...; charset=...">`
_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]+)""", re.I)


def decode_page(body: bytes, header_charset: str | None = None) -> str:
    """ページの本文を文字列にする。**ヘッダ → meta → UTF-8 → cp932 / EUC-JP** の順。

    国内の企業サイトには、Content-Type に charset を付けず `<meta charset=
    "Shift_JIS">` だけで宣言しているものがある。HTTP ライブラリは header に
    charset が無いと UTF-8 で読むので、日本語の商号が化けて「社名が載って
    いない」に数えていた（2026-11 の国内で 606 社中 107 社がそこに入った）。
    """
    for name in (header_charset, _meta_charset(body)):
        codec = _codec(name)
        if codec:
            return body.decode(codec, errors="replace")
    for codec in ("utf-8", "cp932", "euc_jp"):
        try:
            return body.decode(codec)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")


def _meta_charset(body: bytes) -> str | None:
    m = _META_CHARSET.search(body[:8192])
    return m.group(1).decode("ascii", errors="ignore") if m else None


def _codec(name: str | None) -> str | None:
    """宣言された文字コードを Python の名前にする。**Shift_JIS は cp932 で読む**
    （宣言が Shift_JIS でも、実際には機種依存文字を含む cp932 であることが多い）。"""
    if not name:
        return None
    try:
        codec = codecs.lookup(name.strip().lower()).name
    except LookupError:
        return None
    return "cp932" if codec in ("shift_jis", "shift-jis") else codec


def _squash(text: str) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(text or ""))
    return re.sub(r"\s+", "", text).replace("・", "")


def page_title(page: str) -> str:
    """`<title>` と og:title / og:site_name をつなげて返す（NFKC・空白なし）。"""
    parts = re.findall(r"<title[^>]*>(.*?)</title>", page or "", re.S | re.I)
    parts += re.findall(
        r"property=[\"']og:(?:title|site_name)[\"'][^>]*content=[\"']([^\"']*)",
        page or "",
        re.I,
    )
    return "".join(_squash(p) for p in parts)


#: 英字社名でタイトルを確かめるときの、法人格を除いた長さの下限
MIN_EN_TITLE_CHARS = 10

_EN_SUFFIX = re.compile(
    r"(?:CO(?:MPANY)?)?(?:LTD|LIMITED|INC|INCORPORATED|CORP|CORPORATION|KK|HOLDINGS?)$"
)


def _alnum_upper(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", unicodedata.normalize("NFKC", text or "").upper())


def title_names_company_en(page: str, name_en: str | None) -> bool:
    """タイトルに**英字社名がそのまま**載っているか（「SUMIDA CORPORATION」）。

    日本語の商号を書かず英字だけのサイトがある（2026-11、sumida.com）。
    社名全体（法人格込み）か、法人格を除いて 10 文字以上の部分が
    タイトルにあるときだけ採る。短い英字（「RISE」「FUJI」）では決めない。
    """
    full = _alnum_upper(name_en or "")
    if not full:
        return False
    title = _alnum_upper(html.unescape(" ".join(re.findall(
        r"<title[^>]*>(.*?)</title>", page or "", re.S | re.I
    ))))
    if not title:
        return False
    core = _EN_SUFFIX.sub("", full)
    if len(core) >= MIN_EN_TITLE_CHARS and core in title:
        return True
    return len(core) >= MIN_CORE_CHARS + 3 and full in title and full != core


def page_names_company(
    page: str, core: str, *, strict: bool = False, name_en: str | None = None
) -> bool:
    """ページに社名が載っているか。空白の違いは無視する。

    芯が短い（SHORT_CORE_CHARS 以下）ときは、タイトルに商号の形で載って
    いることを求める（モジュールの説明）。`strict` なら芯の長さに関わらず
    タイトルで確かめる（.jp・.com。1組織1つの決まりが無い）。
    英字社名がタイトルにそのまま載っていれば、それでも認める
    （`title_names_company_en`）。
    """
    if title_names_company_en(page, name_en):
        return True
    if len(core) < MIN_CORE_CHARS:
        return False
    if strict or len(core) <= SHORT_CORE_CHARS:
        title = page_title(page).lower()
        return any(form.format(c=core.lower()) in title for form in _TITLE_FORMS)
    return core in _squash(page)


@dataclass
class GuessStats:
    attempted: int = 0
    no_english_name: int = 0
    #: 英字社名が無く、読みから推測した
    from_kana: int = 0
    #: そのうち確かめられた
    found_from_kana: int = 0
    #: 社名の芯が短すぎて、ページで確かめられない
    core_too_short: int = 0
    #: 推測が1つも DNS に無かった
    no_guess_exists: int = 0
    #: 実在したが、ページを1つも読めなかった。**「違った」ではない**（原則5）
    page_unreachable: int = 0
    #: 読めたが、社名が載っていなかった（他社のもの、または別の構成）
    name_not_on_page: int = 0
    found: int = 0
    #: ページは読めなかったが、証明書の組織名（O）が英字社名と一致した
    found_by_cert_org: int = 0
    cert_lookups: int = 0
    #: .co.jp に無く、.jp・.com で確かめられた（`found` の内数）
    found_other_tld: int = 0
    #: 入口が飛ばすだけのページ（meta refresh・フレーム・スクリプト）で、飛び先を読んだ
    meta_refresh_followed: int = 0
    dns_queries: int = 0
    pages_fetched: int = 0
    sample_found: list[str] = field(default_factory=list)
    #: 読めたが社名が無かったページ（ドメインとタイトルの頭）。**何が外れたかを
    #: 次の判断に使う**（2026-11 の国内で 129 社がここに入った）
    sample_not_on_page: list[str] = field(default_factory=list)
    #: 実在したがどの入口も読めなかったドメイン
    sample_unreachable: list[str] = field(default_factory=list)
    #: .jp・.com で確かめたもの。**1組織1つの決まりが無いので、全部見直せる
    #: ように残す**
    sample_found_other_tld: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "no_english_name": self.no_english_name,
            "from_kana": self.from_kana,
            "found_from_kana": self.found_from_kana,
            "core_too_short": self.core_too_short,
            "no_guess_exists": self.no_guess_exists,
            "page_unreachable": self.page_unreachable,
            "name_not_on_page": self.name_not_on_page,
            "found": self.found,
            "found_by_cert_org": self.found_by_cert_org,
            "cert_lookups": self.cert_lookups,
            "found_other_tld": self.found_other_tld,
            "meta_refresh_followed": self.meta_refresh_followed,
            "dns_queries": self.dns_queries,
            "pages_fetched": self.pages_fetched,
            "sample_found": self.sample_found[:5],
            "sample_not_on_page": self.sample_not_on_page[:20],
            "sample_unreachable": self.sample_unreachable[:20],
            "sample_found_other_tld": self.sample_found_other_tld[:20],
        }


class CoJpGuesser:
    """推測して、DNS で実在を見て、ページで確かめる。

    `exists(name)` と `fetch(url)` を差し替えられる（検査はネットワークに出ない）。
    `fetch` は本文を返す。読めなければ None。

    **複数の企業を並行に確かめてよい**（`domain_for` はスレッドから呼べる）。
    数え上げは錠の内側で行う。`exists` と `fetch` も並行に呼ばれる。
    """

    def __init__(
        self,
        exists: Callable[[str], bool],
        fetch: Callable[[str], str | None],
        cert_org: Callable[[str], str | None] | None = None,
    ) -> None:
        self.exists = exists
        self.fetch = fetch
        #: ドメインの TLS 証明書の組織名（O）を返す。無い・読めなければ None
        self.cert_org = cert_org
        self.stats = GuessStats()
        self._lock = threading.Lock()

    def _bump(self, name: str, n: int = 1) -> None:
        with self._lock:
            setattr(self.stats, name, getattr(self.stats, name) + n)

    def domain_for(
        self, name_ja: str | None, name_en: str | None, kana: str | None = None
    ) -> str | None:
        self._bump("attempted")
        candidates = guesses(name_en)
        by_kana = False
        if not candidates:
            # **英字社名が無ければ読みから。** 無ければ今までどおり諦める
            candidates = kana_guesses(kana)
            if not candidates:
                self._bump("no_english_name")
                return None
            by_kana = True
            self._bump("from_kana")
        core = name_core(name_ja)
        if len(core) < MIN_CORE_CHARS:
            self._bump("core_too_short")
            return None

        outcome = self._attempt(candidates, core, name_en, by_kana=by_kana, strict=False)
        if isinstance(outcome, str):
            return outcome
        # **.co.jp に無ければ .jp・.com も見る。** 2026-11 の国内で起点が
        # 取れなかった 341 社には、ricksoft.jp・modalis.com・cyfuse.com の
        # ように .co.jp を持たない新しめの会社が並んでいた。ただし .jp・.com は
        # 1組織1つの決まりが無く、同じ語を他社や個人が持っている。**社名の長さに
        # 関わらずタイトルに商号の形で載っていることを求める**（`strict`）
        other = self._attempt(
            other_tld_guesses(candidates), core, name_en, by_kana=by_kana, strict=True
        )
        if isinstance(other, str):
            with self._lock:
                self.stats.found_other_tld += 1
                if len(self.stats.sample_found_other_tld) < 20:
                    self.stats.sample_found_other_tld.append(f"{other} ← {core}")
            return other
        # 確かめられなかった理由は1つに数える。**読めて違った > 読めなかった >
        # DNS に無かった** の順に、分かったことの多い方を採る
        final = max(outcome, other, key=lambda o: _OUTCOME_RANK[o[0]])
        kind, detail = final
        if kind == "not_on_page":
            self._bump("name_not_on_page")
            if detail is not None:
                with self._lock:
                    if len(self.stats.sample_not_on_page) < 20:
                        self.stats.sample_not_on_page.append(
                            f"{detail[0]} 「{detail[1]}」 ← {core}"
                        )
        elif kind == "unreachable":
            self._bump("page_unreachable")
            with self._lock:
                if len(self.stats.sample_unreachable) < 20:
                    self.stats.sample_unreachable.append(detail)
        else:
            self._bump("no_guess_exists")
        return None

    def _attempt(
        self,
        candidates: list[str],
        core: str,
        name_en: str | None,
        *,
        by_kana: bool,
        strict: bool,
    ) -> str | tuple[str, Any]:
        """推測の並びを1つ確かめる。見つかればドメイン、だめなら (理由, 詳細)。"""
        existing = []
        for domain in candidates:
            self._bump("dns_queries")
            if self.exists(domain):
                existing.append(domain)
        if not existing:
            return ("none", None)

        read_any = False
        first_read: tuple[str, str] | None = None
        for domain in existing:
            # **https で読めなければ http も見る。** 中間証明書を送っていない
            # サイト（ブラウザは補うが、こちらの検証は通らない）や、http しか
            # 出していないサイトが「読めない」に入っていた（2026-11 の国内で
            # 606 社中 97 社）。確かめる中身（日本語の商号）は同じである
            for url in (
                f"https://www.{domain}/",
                f"https://{domain}/",
                f"http://www.{domain}/",
                f"http://{domain}/",
            ):
                self._bump("pages_fetched")
                page = self.fetch(url)
                if page is None:
                    continue
                read_any = True
                named = page_names_company(page, core, strict=strict, name_en=name_en)
                # **入口が「飛ばすだけのページ」なら、飛び先を1回だけ読む。**
                # `<meta http-equiv="refresh" content="0;URL=/top/">`、フレーム、
                # スクリプトの location だけでタイトルも本文も無いトップページが
                # ある（2026-11 の国内で、読めて違ったものの見本 20 件中 3 件が
                # タイトル空だった）。入口で確かめられなかったときだけ、飛び先が
                # 同じドメインの中のときだけ
                if not named:
                    target = entry_redirect_target(page, url, domain)
                    if target is not None:
                        self._bump("pages_fetched")
                        self._bump("meta_refresh_followed")
                        followed = self.fetch(target)
                        if followed is not None:
                            page = followed
                            named = page_names_company(
                                page, core, strict=strict, name_en=name_en
                            )
                if first_read is None:
                    first_read = (domain, page_title(page)[:40])
                if named:
                    with self._lock:
                        self.stats.found += 1
                        if by_kana:
                            self.stats.found_from_kana += 1
                        if len(self.stats.sample_found) < 5:
                            self.stats.sample_found.append(domain)
                    return domain
                # 1つ読めて載っていなければ、同じドメインの別の入口は見ない
                break
        if read_any:
            return ("not_on_page", first_read)
        # **ページが読めなくても、証明書は読める。** 企業サイトの WAF は
        # 見慣れない User-Agent を 403 で断ることが多いが（ブラウザを
        # 装うことはしない）、TLS の握手はその前に終わっている。OV / EV
        # 証明書の組織名（O）は CA が法人を確かめて入れる欄なので、
        # 英字社名と**完全に**一致すれば、そのドメインはその会社のもの
        # と言える（2026-11 の国内で、読めなかった 64 社に secuavail.co.jp・
        # nireco.co.jp など本物らしいものが並んでいた）
        if self.cert_org is not None and name_en:
            for domain in existing:
                self._bump("cert_lookups")
                if org_matches(self.cert_org(domain), name_en):
                    with self._lock:
                        self.stats.found += 1
                        self.stats.found_by_cert_org += 1
                        if len(self.stats.sample_found) < 5:
                            self.stats.sample_found.append(domain)
                    return domain
        return ("unreachable", existing[0])
