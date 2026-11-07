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
SUBS_FIELDS = ("edinet_code", "doc_id", "subsidiary", "scanned_on")
SUBS_CACHE_VERSION = 1

#: 節の終わり（次の節の見出し）
_SECTION_END = ("従業員の状況", "【従業員", "第2【事業の状況】", "事業の状況】")
#: 読む範囲の上限（文字）
_WINDOW = 40_000
#: 1社あたりに残す数の上限
MAX_PER_COMPANY = 300

_LEGAL = "株式会社|有限会社|合同会社"
_NAME = re.compile(
    rf"((?:{_LEGAL})[^\s（(、,，。・※*＊注]{{1,30}}|[^\s（(、,，。・※*＊注]{{1,30}}?(?:{_LEGAL}))"
)
#: 名前でない語（見出し・注記に出る）
_NOT_A_NAME = ("連結子会社", "持分法", "関連会社", "その他", "親会社", "当社", "子会社")


def subsidiary_names(text: str, own_name: str | None = None) -> list[str]:
    """「関係会社の状況」の節に出てくる国内の法人格付きの名前（出てきた順、重複なし）。

    見出しは目次にも出るので、**名前が見つかった最初の出現**を使う。自社の名前は除く。
    """
    own = unicodedata.normalize("NFKC", own_name or "").replace(" ", "")
    start = 0
    while True:
        i = text.find("関係会社の状況", start)
        if i < 0:
            return []
        window = text[i : i + _WINDOW]
        ends = [window.find(e, 20) for e in _SECTION_END]
        ends = [e for e in ends if e > 0]
        if ends:
            window = window[: min(ends)]
        out: list[str] = []
        for raw in _NAME.findall(unicodedata.normalize("NFKC", window)):
            name = raw.strip().replace(" ", "")
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
        if out:
            return out
        start = i + 1


def _cache(reader: EdinetKoukoku, doc_id: str) -> Path | None:
    if reader.cache_dir is None:
        return None
    return reader.cache_dir / "subs" / f"{doc_id}.json"


def read_subsidiaries(
    reader: EdinetKoukoku, doc_id: str, own_name: str | None
) -> list[str] | None:
    """1件の有報から子会社名。引けなければ None。キャッシュは有報ごと。"""
    path = _cache(reader, doc_id)
    if path is not None and path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("version") == SUBS_CACHE_VERSION:
                return list(data["names"])
        except (OSError, ValueError, KeyError):
            pass
    try:
        text = document_text(reader.api_get(f"/documents/{doc_id}", {"type": "1"}))
    except Exception:  # noqa: BLE001 - 1件の失敗で全体を止めない
        return None
    names = subsidiary_names(text, own_name)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"version": SUBS_CACHE_VERSION, "names": names}, ensure_ascii=False),
            encoding="utf-8",
        )
    return names


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


def write_csv(path: str | Path, data: dict[str, tuple[str, list[str], str]]) -> None:
    """**子会社の無い会社も1行残す**（読んだが無かった、を覚えておく）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(SUBS_FIELDS)
        for code in sorted(data):
            doc, names, on = data[code]
            for name in names or [""]:
                w.writerow([code, doc, name, on])


def scan(
    reader: EdinetKoukoku,
    companies: Iterable[tuple[str, str | None]],
    existing: dict[str, tuple[str, list[str], str]],
    *,
    max_docs: int,
) -> tuple[dict[str, tuple[str, list[str], str]], dict[str, int]]:
    """全社の最新の有報から子会社名を読む。**新しい有報が出た会社だけ読み直す。**"""
    names_by_code = {c: n for c, n in companies if c}
    latest = reader.latest_yuho(set(names_by_code))
    out = dict(existing)
    stats = {"companies": len(names_by_code), "with_yuho": len(latest), "already": 0,
             "read": 0, "with_subsidiaries": 0, "subsidiaries": 0, "deferred": 0, "failed": 0}
    for code in sorted(latest):
        doc_id = latest[code]
        if code in existing and existing[code][0] == doc_id:
            stats["already"] += 1
            continue
        if stats["read"] >= max_docs:
            stats["deferred"] += 1
            continue
        got = read_subsidiaries(reader, doc_id, names_by_code.get(code))
        stats["read"] += 1
        if got is None:
            stats["failed"] += 1
            continue
        if got:
            stats["with_subsidiaries"] += 1
            stats["subsidiaries"] += len(got)
        out[code] = (doc_id, got, reader.today.isoformat())
    return out, stats
