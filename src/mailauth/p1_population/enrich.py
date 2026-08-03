"""identity の補完（enrich）。

gBizINFO で official_url を、国税庁法人番号 Web-API で商号・所在地の裏取りを行う。
どちらも認証情報が無ければ丸ごとスキップし、その事実を manifest に残す。
「取れなかった」ことを黙って欠損として扱わない（原則5の精神）。

キャッシュは法人番号単位でディスクに置く。月次で同じ法人番号を何度も
問い合わせる意味はなく、相手のAPIに対する作法としても悪い。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from ..config import credential
from ..paths import cache_root

GBIZINFO_ENDPOINT = "https://info.gbiz.go.jp/hojin/v1/hojin"
HOUJIN_BANGOU_ENDPOINT = "https://api.houjin-bangou.nta.go.jp/4/num"

USER_AGENT = "mailauth-observatory/0.1 (+https://github.com/suminot/mailauth-observatory)"


@dataclass
class EnrichResult:
    """1エンリッチャの結果。値は法人番号をキーにした辞書。"""

    values: dict[str, dict[str, str]] = field(default_factory=dict)
    attempted: int = 0
    hit: int = 0
    miss: int = 0
    error: int = 0
    skipped_reason: str | None = None
    cache_hit: int = 0


class GbizInfoClient:
    """gBizINFO 法人基本情報。company_url（企業公式ホームページURL）を取る。

    政府標準利用規約2.0準拠。出典表記が必要（母集団設定の attribution に記載）。
    """

    def __init__(
        self,
        token: str | None = None,
        *,
        qps: float = 5.0,
        timeout: float = 20.0,
        cache_dir: Path | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.token = token or credential("MAILAUTH_GBIZINFO_TOKEN")
        self.min_interval = 1.0 / qps if qps > 0 else 0.0
        self.timeout = timeout
        self.cache_dir = cache_dir or (cache_root() / "gbizinfo")
        self._client = client
        self._last_call = 0.0

    def _cache_path(self, houjin_bangou: str) -> Path:
        return self.cache_dir / f"{houjin_bangou}.json"

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call = time.monotonic()

    def fetch(self, houjin_bangou_list: list[str], *, limit: int | None = None) -> EnrichResult:
        result = EnrichResult()
        if not self.token:
            result.skipped_reason = (
                "MAILAUTH_GBIZINFO_TOKEN が未設定のため official_url を取得していない"
            )
            return result

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        targets = houjin_bangou_list[:limit] if limit else houjin_bangou_list
        client = self._client or httpx.Client(timeout=self.timeout)
        close = self._client is None

        try:
            for bangou in targets:
                result.attempted += 1
                cached = self._cache_path(bangou)
                if cached.is_file():
                    try:
                        payload = json.loads(cached.read_text(encoding="utf-8"))
                        result.cache_hit += 1
                    except (json.JSONDecodeError, OSError):
                        payload = None
                else:
                    payload = None

                if payload is None:
                    self._throttle()
                    try:
                        resp = client.get(
                            f"{GBIZINFO_ENDPOINT}/{bangou}",
                            headers={
                                "X-hojinInfo-api-token": self.token,
                                "Accept": "application/json",
                                "User-Agent": USER_AGENT,
                            },
                        )
                    except httpx.HTTPError:
                        result.error += 1
                        continue
                    if resp.status_code == 404:
                        result.miss += 1
                        cached.write_text("{}", encoding="utf-8")
                        continue
                    if resp.status_code != 200:
                        result.error += 1
                        continue
                    try:
                        payload = resp.json()
                    except ValueError:
                        result.error += 1
                        continue
                    cached.write_text(
                        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
                    )

                info = (payload or {}).get("hojin-infos") or []
                url = (info[0].get("company_url") if info else None) or None
                if url:
                    result.values[bangou] = {"official_url": url}
                    result.hit += 1
                else:
                    result.miss += 1
        finally:
            if close:
                client.close()

        return result


class HoujinBangouClient:
    """国税庁 法人番号 Web-API。商号・所在地の裏取りに使う。

    業種情報は含まないので、業種は EDINET 側の提出者業種を使う。
    1リクエストで最大10件まで問い合わせられる。
    """

    BATCH_SIZE = 10

    def __init__(
        self,
        app_id: str | None = None,
        *,
        qps: float = 2.0,
        timeout: float = 20.0,
        cache_dir: Path | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.app_id = app_id or credential("MAILAUTH_HOUJIN_BANGOU_APP_ID")
        self.min_interval = 1.0 / qps if qps > 0 else 0.0
        self.timeout = timeout
        self.cache_dir = cache_dir or (cache_root() / "houjin_bangou")
        self._client = client
        self._last_call = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call = time.monotonic()

    def fetch(self, houjin_bangou_list: list[str], *, limit: int | None = None) -> EnrichResult:
        result = EnrichResult()
        if not self.app_id:
            result.skipped_reason = (
                "MAILAUTH_HOUJIN_BANGOU_APP_ID が未設定のため商号・所在地の裏取りをしていない"
            )
            return result

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        targets = houjin_bangou_list[:limit] if limit else houjin_bangou_list
        client = self._client or httpx.Client(timeout=self.timeout)
        close = self._client is None

        try:
            for i in range(0, len(targets), self.BATCH_SIZE):
                batch = targets[i : i + self.BATCH_SIZE]
                pending = []
                for bangou in batch:
                    result.attempted += 1
                    cached = self.cache_dir / f"{bangou}.json"
                    if cached.is_file():
                        try:
                            payload = json.loads(cached.read_text(encoding="utf-8"))
                        except (json.JSONDecodeError, OSError):
                            pending.append(bangou)
                            continue
                        result.cache_hit += 1
                        if payload:
                            result.values[bangou] = payload
                            result.hit += 1
                        else:
                            result.miss += 1
                    else:
                        pending.append(bangou)

                if not pending:
                    continue

                self._throttle()
                try:
                    resp = client.get(
                        HOUJIN_BANGOU_ENDPOINT,
                        params={
                            "id": self.app_id,
                            "number": ",".join(pending),
                            "type": "12",  # CSV/Shift-JIS
                        },
                        headers={"User-Agent": USER_AGENT},
                    )
                except httpx.HTTPError:
                    result.error += len(pending)
                    continue
                if resp.status_code != 200:
                    result.error += len(pending)
                    continue

                found = _parse_houjin_csv(resp.content)
                for bangou in pending:
                    payload = found.get(bangou, {})
                    (self.cache_dir / f"{bangou}.json").write_text(
                        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
                    )
                    if payload:
                        result.values[bangou] = payload
                        result.hit += 1
                    else:
                        result.miss += 1
        finally:
            if close:
                client.close()

        return result


def _parse_houjin_csv(body: bytes) -> dict[str, dict[str, str]]:
    """法人番号 Web-API の CSV 応答を法人番号 -> {name, address} に変換する。

    列構成: 1=通番, 2=法人番号, ..., 7=商号又は名称, ...
    仕様変更に強くするため、13桁数字を法人番号、その後で最初に現れる
    非数字の長い文字列を商号として拾う保守的な読み方をする。
    """
    import csv as _csv
    import io as _io

    text = body.decode("cp932", errors="replace")
    out: dict[str, dict[str, str]] = {}
    for row in _csv.reader(_io.StringIO(text)):
        if len(row) < 7:
            continue
        bangou = row[1].strip() if len(row) > 1 else ""
        if len(bangou) != 13 or not bangou.isdigit():
            continue
        name = row[6].strip() if len(row) > 6 else ""
        address = "".join(row[9:12]).strip() if len(row) > 11 else ""
        entry: dict[str, str] = {}
        if name:
            entry["nta_name"] = name
        if address:
            entry["nta_address"] = address
        out[bangou] = entry
    return out
