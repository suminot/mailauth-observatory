"""EDINETコードリストの取得と解析。

EDINETコードリスト（EdinetcodeDlInfo.csv）1本で
「法人番号 + 証券コード + 提出者名 + 業種」が同時に揃う。
証券コードが埋まっているレコードを抽出すれば上場企業が取れる。
JPX の data_j.xls には一切触れない（DESIGN.md 1.4）。

スクレイピングは禁止されているので、配信されている ZIP を
そのまま取得する経路だけを実装する。HTML を辿ってリンクを探すような
処理はここに書かないこと。
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..config import EdinetSourceConfig, credential
from ..paths import cache_root

USER_AGENT = "mailauth-observatory/0.1 (+https://github.com/suminot/mailauth-observatory)"

#: EDINET コードリストの標準列数。これを下回る行は不正行として弾く。
EXPECTED_COLUMNS = 13


class EdinetError(RuntimeError):
    pass


@dataclass
class EdinetRow:
    """EDINETコードリストの1行（必要な列のみ）。"""

    edinet_code: str
    submitter_type: str
    listing_status: str
    name: str
    name_en: str
    address: str
    industry: str
    securities_code: str
    houjin_bangou: str


@dataclass
class FetchResult:
    path: Path
    from_cache: bool
    fetched_at: dt.datetime
    bytes: int


def fetch_code_list(
    cfg: EdinetSourceConfig,
    *,
    cache_ttl_hours: int = 24,
    source_file: Path | str | None = None,
    timeout: float = 60.0,
) -> FetchResult:
    """コードリストの ZIP を取得する。

    `source_file` が指定されていればネットワークに出ない。
    取得済みで TTL 内ならキャッシュを再利用する。同じ月に2回実行しても
    同じ入力になる（原則6）ようにするためで、帯域の節約は副次的な理由。
    """
    if source_file:
        p = Path(source_file)
        if not p.is_file():
            raise EdinetError(f"指定されたソースファイルがありません: {p}")
        return FetchResult(
            path=p, from_cache=True, fetched_at=dt.datetime.now(dt.UTC), bytes=p.stat().st_size
        )

    cache_dir = cache_root() / "edinet"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / "Edinetcode.zip"

    if cached.is_file():
        age_h = (dt.datetime.now().timestamp() - cached.stat().st_mtime) / 3600
        if age_h < cache_ttl_hours:
            return FetchResult(
                path=cached,
                from_cache=True,
                fetched_at=dt.datetime.fromtimestamp(cached.stat().st_mtime, dt.UTC),
                bytes=cached.stat().st_size,
            )

    params: dict[str, str] = {}
    if cfg.send_subscription_key:
        key = credential("MAILAUTH_EDINET_SUBSCRIPTION_KEY")
        if key:
            params[cfg.subscription_key_param] = key

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(cfg.url, params=params, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            body = resp.content
    except httpx.HTTPError as exc:
        if cached.is_file():
            # 期限切れでも、取得に失敗したなら古いキャッシュで走らせるほうがよい。
            # 呼び出し側は from_cache を見て manifest に警告を出すこと。
            return FetchResult(
                path=cached,
                from_cache=True,
                fetched_at=dt.datetime.fromtimestamp(cached.stat().st_mtime, dt.UTC),
                bytes=cached.stat().st_size,
            )
        raise EdinetError(f"EDINETコードリストの取得に失敗しました: {exc}") from exc

    if not body[:2] == b"PK":
        # キーが要る配信先に無キーで当たると HTML のエラーページが返ることがある。
        raise EdinetError(
            "取得した内容が ZIP ではありません。"
            "MAILAUTH_EDINET_SUBSCRIPTION_KEY を設定しているか、URL が正しいか確認してください"
        )

    cached.write_bytes(body)
    return FetchResult(
        path=cached, from_cache=False, fetched_at=dt.datetime.now(dt.UTC), bytes=len(body)
    )


def _read_csv_bytes(path: Path) -> bytes:
    """ZIP なら中の CSV を、CSV ならそのものを返す。"""
    data = path.read_bytes()
    if data[:2] != b"PK":
        return data
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not names:
            raise EdinetError(f"ZIP に CSV が含まれていません: {path}")
        # EdinetcodeDlInfo.csv を優先。無ければ最初の CSV
        target = next((n for n in names if "edinetcode" in n.lower()), names[0])
        return zf.read(target)


def parse_code_list(path: Path, cfg: EdinetSourceConfig) -> tuple[list[EdinetRow], dict[str, int]]:
    """コードリストを解析して行のリストと統計を返す。

    列位置は設定（configs/populations/*.yaml）から取る。EDINET 側の
    列構成が変わったときにコードを触らずに追随できるようにするため。
    """
    raw = _read_csv_bytes(path)
    text = raw.decode(cfg.encoding, errors="replace")
    reader = csv.reader(io.StringIO(text))

    col = cfg.columns
    required = [
        "edinet_code",
        "name",
        "industry",
        "securities_code",
        "houjin_bangou",
    ]
    missing = [k for k in required if k not in col]
    if missing:
        raise EdinetError(f"列位置の設定が足りません: {missing}")

    rows: list[EdinetRow] = []
    stats = {"total_lines": 0, "skipped_header": 0, "skipped_malformed": 0}

    def get(record: list[str], key: str) -> str:
        idx = col.get(key)
        if idx is None or idx >= len(record):
            return ""
        return (record[idx] or "").strip()

    for i, record in enumerate(reader):
        stats["total_lines"] += 1
        if i <= cfg.header_row:
            # 1行目はタイトル行、2行目がヘッダ行（header_row=1）
            stats["skipped_header"] += 1
            continue
        if len(record) < EXPECTED_COLUMNS:
            stats["skipped_malformed"] += 1
            continue
        rows.append(
            EdinetRow(
                edinet_code=get(record, "edinet_code"),
                submitter_type=get(record, "submitter_type"),
                listing_status=get(record, "listing_status"),
                name=get(record, "name"),
                name_en=get(record, "name_en"),
                address=get(record, "address"),
                industry=get(record, "industry"),
                securities_code=get(record, "securities_code"),
                houjin_bangou=get(record, "houjin_bangou"),
            )
        )

    return rows, stats
