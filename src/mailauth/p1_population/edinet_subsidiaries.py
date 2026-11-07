"""有報の「関係会社の状況」から、国内の子会社・関連会社の商号を読む（BACKLOG 52）。

**グループ会社のドメインを、上場会社の数字に混ぜずに別に数えるための一歩目。**
TwoFive の日経225 の調査は「管理・運用する」ドメインを数えており、グループ会社の
ものを含むと見られる。こちらは上場会社そのもののドメインに限っている。

ここでは商号を読むだけで、ドメインは引かない。読むのは国内の法人格（株式会社・
有限会社・合同会社）の付いた名前だけ ── 海外の子会社は英字の表記揺れが大きく、
名前からドメインを確かめる手段（`.co.jp` の推測）が使えない。

有報は EDINET API だけで読む（1秒に1本、`EdinetKoukoku` と同じ取得口とキャッシュ）。
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections.abc import Iterable
from pathlib import Path

from .edinet_koukoku import EdinetKoukoku, document_text

SUBS_CSV = "configs/domains/subsidiaries.csv"
SUBS_FIELDS = ("edinet_code", "doc_id", "subsidiary", "scanned_on", "parser", "prefecture")
SUBS_CACHE_VERSION = 2

#: 節の終わり（次の節の見出し）
_SECTION_END = ("従業員の状況", "【従業員", "第2【事業の状況】", "事業の状況】")
#: 読む範囲の上限（文字）
_WINDOW = 40_000
#: 1社あたりに残す数の上限
MAX_PER_COMPANY = 300
#: 名前の読み方を変えたら上げる。**上がったら、読んだ有報も読み直す**（節の本文は
#: キャッシュにあるので、取り直しは要らない）
SUBS_PARSER_VERSION = 4

_LEGAL = "株式会社|有限会社|合同会社"
_NAME = re.compile(
    rf"((?:{_LEGAL})[^\s（(、,，。・※*＊注]{{1,30}}|[^\s（(、,，。・※*＊注]{{1,30}}?(?:{_LEGAL}))"
)
#: 名前でない語（見出し・注記に出る）
_NOT_A_NAME = ("連結子会社", "持分法", "関連会社", "その他", "親会社", "当社", "子会社")
#: **名前の前後にくっつく地の文**（注記の「前連結会計年度において東京ガス株式会社」
#: 「株式会社A及び株式会社B」）。名前の区切りとして扱う。長いものから当てる
_CONNECTORS = sorted((
    "及び", "および", "並びに", "ならびに", "において", "について", "に伴い", "に伴う",
    "により", "による", "であった", "である", "していた", "しており", "しております",
    "おります", "した", "して", "する", "を連結", "を除外", "を設立", "を新たに", "新たに",
    "の全株式", "を保有", "が保有", "が所有", "を所有", "を取得", "当該", "前連結会計年度",
    "当連結会計年度", "連結の範囲", "出資により", "を100%", "の決算日", "決算日",
), key=len, reverse=True)
_CONNECTOR_RE = re.compile("|".join(re.escape(c) for c in _CONNECTORS))
#: 表の見出し。子会社の区分だけ読む（持分法の関連会社・親会社・その他の関係会社は
#: グループ会社ではない）
_GROUP_HEADING = re.compile(
    r"[(（]\s*(連結子会社|非連結子会社|持分法適用非連結子会社|持分法適用関連会社|"
    r"持分法非適用関連会社|関連会社|その他の関係会社|親会社)\s*[)）]"
)
_SUBSIDIARY_HEADINGS = ("連結子会社", "非連結子会社", "持分法適用非連結子会社")


def _section(text: str) -> str:
    """「関係会社の状況」の節の本文。見出しは目次にも出るので、**名前が見つかる
    最初の出現**を使う。【】付きの見出しを先に探す。無ければ空。"""
    norm = unicodedata.normalize("NFKC", text)
    for marker in ("【関係会社の状況】", "関係会社の状況"):
        start = 0
        while True:
            i = norm.find(marker, start)
            if i < 0:
                break
            window = norm[i : i + _WINDOW]
            ends = [window.find(e, 20) for e in _SECTION_END]
            ends = [e for e in ends if e > 0]
            if ends:
                window = window[: min(ends)]
            if _NAME.search(window):
                return window
            start = i + 1
    return ""


def _subsidiary_part(section: str) -> str:
    """表の区分の見出しがあれば、子会社の区分だけをつなぐ。無ければ節の全部。"""
    marks = list(_GROUP_HEADING.finditer(section))
    if not marks:
        return section
    parts = []
    for k, m in enumerate(marks):
        if m.group(1) in _SUBSIDIARY_HEADINGS:
            stop = marks[k + 1].start() if k + 1 < len(marks) else len(section)
            parts.append(section[m.end() : stop])
    return " ".join(parts)


#: 名前の前に付いた住所（「東京都千代田区丸の内一丁目4番5号三菱UFJ信託銀行株式会社」）
_ADDRESS_PREFIX = re.compile(r"^.*?\d+(?:号|番地)")
#: 「株式会社X」の X の後ろに付いた助詞（「株式会社日本カストディ銀行は、」）
_TRAILING_PARTICLE = re.compile(rf"^((?:{_LEGAL}).{{2,}}?)[はがをにでへも]$")


def _trim(name: str) -> str:
    name = _ADDRESS_PREFIX.sub("", name)
    m = _TRAILING_PARTICLE.match(name)
    return m.group(1) if m else name


def names_from_section(section: str, own_name: str | None = None) -> list[str]:
    own = unicodedata.normalize("NFKC", own_name or "").replace(" ", "")
    body = _CONNECTOR_RE.sub("、", _subsidiary_part(section))
    out: list[str] = []
    for raw in _NAME.findall(body):
        name = _trim(raw.strip().replace(" ", ""))
        core = re.sub(_LEGAL, "", name)
        # 注記の番号（「(注)1.」）や記号だけのものは名前ではない
        if len(re.sub(r"[\W\d_]", "", core)) < 2 or not re.match(r"[\w]", name):
            continue
        if any(w in core for w in _NOT_A_NAME):
            continue
        if own and name == own:
            continue
        if name not in out:
            out.append(name)
        if len(out) >= MAX_PER_COMPANY:
            break
    return out


PREFECTURES = (
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県", "茨城県", "栃木県",
    "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県", "新潟県", "富山県", "石川県", "福井県",
    "山梨県", "長野県", "岐阜県", "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府",
    "兵庫県", "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県", "徳島県",
    "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県",
    "鹿児島県", "沖縄県",
)
#: 都道府県を書かずに市から書く表がある（「大阪市」「横浜市」）。政令指定都市だけ
_CITY_PREFECTURE = {
    "札幌市": "北海道", "仙台市": "宮城県", "さいたま市": "埼玉県", "千葉市": "千葉県",
    "横浜市": "神奈川県", "川崎市": "神奈川県", "相模原市": "神奈川県", "新潟市": "新潟県",
    "静岡市": "静岡県", "浜松市": "静岡県", "名古屋市": "愛知県", "京都市": "京都府",
    "大阪市": "大阪府", "堺市": "大阪府", "神戸市": "兵庫県", "岡山市": "岡山県",
    "広島市": "広島県", "北九州市": "福岡県", "福岡市": "福岡県", "熊本市": "熊本県",
}
_SHORT_PREF = {p[:-1]: p for p in PREFECTURES if p.endswith(("都", "府", "県"))} | {
    "北海道": "北海道"}


def prefecture_of(token: str) -> str | None:
    """住所の欄の先頭から都道府県。書いていなければ政令指定都市から。無ければ None。"""
    t = token.strip()
    for p in PREFECTURES:
        if t.startswith(p):
            return p
    for city, p in _CITY_PREFECTURE.items():
        if t.startswith(city):
            return p
    # 「東京」「大阪」だけのこともある（都府県を省いた表）
    for short, p in _SHORT_PREF.items():
        if len(short) >= 2 and t.startswith(short) and not t.startswith(short + "市"):
            return p
    return None


def places_from_section(section: str, names: list[str]) -> dict[str, str]:
    """子会社名ごとに、表の次の欄（住所）の都道府県。同じ商号の法人を見分けるのに使う。"""
    body = unicodedata.normalize("NFKC", section)
    out: dict[str, str] = {}
    for name in names:
        i = body.find(name)
        if i < 0:
            continue
        rest = body[i + len(name) : i + len(name) + 60]
        # 注記の記号（※1・(注)2）は飛ばす
        rest = re.sub(r"^\s*(?:[※*＊]\d*|\(注\)\d*|\d\))?\s*", "", rest)
        pref = prefecture_of(rest)
        if pref:
            out[name] = pref
    return out


def section_places(reader: EdinetKoukoku, doc_id: str, names: list[str]) -> dict[str, str]:
    """キャッシュにある節の本文から、子会社名ごとの都道府県。"""
    section = cached_section(reader, doc_id)
    return places_from_section(section, names) if section else {}


def subsidiary_names(text: str, own_name: str | None = None) -> list[str]:
    """「関係会社の状況」の節の子会社の区分に出てくる国内の法人格付きの名前
    （出てきた順、重複なし）。自社の名前は除く。"""
    return names_from_section(_section(text), own_name)


def _cache(reader: EdinetKoukoku, doc_id: str) -> Path | None:
    if reader.cache_dir is None:
        return None
    return reader.cache_dir / "subs" / f"{doc_id}.json"


def cached_section(reader: EdinetKoukoku, doc_id: str) -> str | None:
    """キャッシュにある節の本文。無ければ None。"""
    path = _cache(reader, doc_id)
    if path is None or not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("version") != SUBS_CACHE_VERSION or not isinstance(data.get("section"), str):
        return None
    return data["section"]


def read_subsidiaries(
    reader: EdinetKoukoku, doc_id: str, own_name: str | None
) -> list[str] | None:
    """1件の有報から子会社名。引けなければ None。

    キャッシュには**節の本文**を置く。名前の読み方を変えても取り直さずに読み直せる。
    """
    section = cached_section(reader, doc_id)
    if section is not None:
        return names_from_section(section, own_name)
    try:
        text = document_text(reader.api_get(f"/documents/{doc_id}", {"type": "1"}))
    except Exception:  # noqa: BLE001 - 1件の失敗で全体を止めない
        return None
    section = _section(text)
    path = _cache(reader, doc_id)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"version": SUBS_CACHE_VERSION, "section": section}, ensure_ascii=False),
            encoding="utf-8",
        )
    return names_from_section(section, own_name)


def read_csv(path: str | Path) -> dict[str, tuple[str, list[str], str]]:
    p = Path(path)
    if not p.exists():
        return {}
    out: dict[str, tuple[str, list[str], str]] = {}
    with p.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            code = (row.get("edinet_code") or "").strip()
            if not code:
                continue
            doc, names, on = out.get(code, (row.get("doc_id") or "", [], ""))
            name = (row.get("subsidiary") or "").strip()
            if name and name not in names:
                names.append(name)
            out[code] = (doc or row.get("doc_id") or "", names, row.get("scanned_on") or on)
    return out


def read_places(path: str | Path) -> dict[tuple[str, str], str]:
    """(EDINET コード, 子会社名) → 都道府県（表の住所の欄から）。"""
    p = Path(path)
    if not p.exists():
        return {}
    with p.open(encoding="utf-8", newline="") as f:
        return {
            (row["edinet_code"], row["subsidiary"]): row["prefecture"]
            for row in csv.DictReader(f)
            if row.get("subsidiary") and row.get("prefecture")
        }


def csv_parser_version(path: str | Path) -> int | None:
    """一覧を書いたときの名前の読み方の版。列が無い（古い一覧）・空なら None。"""
    p = Path(path)
    if not p.exists():
        return SUBS_PARSER_VERSION
    with p.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            try:
                return int(row.get("parser") or "")
            except ValueError:
                return None
    return SUBS_PARSER_VERSION


def write_csv(
    path: str | Path,
    data: dict[str, tuple[str, list[str], str]],
    places: dict[tuple[str, str], str] | None = None,
) -> None:
    """**子会社の無い会社も1行残す**（読んだが無かった、を覚えておく）。"""
    places = places or {}
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(SUBS_FIELDS)
        for code in sorted(data):
            doc, names, on = data[code]
            for name in names or [""]:
                w.writerow([code, doc, name, on, SUBS_PARSER_VERSION,
                            places.get((code, name), "")])


def scan(
    reader: EdinetKoukoku,
    companies: Iterable[tuple[str, str | None]],
    existing: dict[str, tuple[str, list[str], str]],
    *,
    max_docs: int,
    parsed_with: int | None = SUBS_PARSER_VERSION,
    places: dict[tuple[str, str], str] | None = None,
) -> tuple[dict[str, tuple[str, list[str], str]], dict[str, int]]:
    """全社の最新の有報から子会社名を読む。**新しい有報が出た会社だけ読み直す。**

    `parsed_with` は一覧を書いたときの読み方の版。今の版と違えば（古い一覧）、
    読んだ有報も読み直す。`max_docs` は EDINET から取る本数の上限
    （キャッシュから読み直す分は数えない）。
    """
    names_by_code = {c: n for c, n in companies if c}
    latest = reader.latest_yuho(set(names_by_code))
    reparse = parsed_with != SUBS_PARSER_VERSION
    out = dict(existing)
    stats = {"companies": len(names_by_code), "with_yuho": len(latest), "already": 0,
             "read": 0, "reparsed": 0, "with_subsidiaries": 0, "subsidiaries": 0,
             "deferred": 0, "failed": 0}
    for code in sorted(latest):
        doc_id = latest[code]
        if code in existing and existing[code][0] == doc_id and not reparse:
            stats["already"] += 1
            continue
        cached = cached_section(reader, doc_id) is not None
        if not cached and stats["read"] >= max_docs:
            stats["deferred"] += 1
            # 古い読み方の名前は残さない（一覧には今の版で書くので、残すと
            # 次に読み直す対象から外れる）。次の回に新しく読む
            if reparse:
                out.pop(code, None)
            continue
        got = read_subsidiaries(reader, doc_id, names_by_code.get(code))
        stats["reparsed" if cached else "read"] += 1
        if got is None:
            stats["failed"] += 1
            continue
        if got:
            stats["with_subsidiaries"] += 1
            stats["subsidiaries"] += len(got)
            if places is not None:
                for name, pref in section_places(reader, doc_id, got).items():
                    places[(code, name)] = pref
        out[code] = (doc_id, got, reader.today.isoformat())
    return out, stats
