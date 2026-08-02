"""設定の読み込み（原則7 ── 設定はコード外に出す）。

母集団の選択、DKIMセレクタ辞書、フィンガープリント規則、業種マッピングは
すべて YAML / CSV としてリポジトリに置く。コードを触らずに対象や判定ルールを
変えられることが要件なので、ここではスキーマ検証だけを行い、
値そのものはコードに書かない。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .paths import config_path, repo_root

# --------------------------------------------------------------------------
# .env
# --------------------------------------------------------------------------


def load_dotenv(path: Path | None = None) -> dict[str, str]:
    """.env を読んで os.environ に反映する（既存の環境変数は上書きしない）。

    python-dotenv を足すほどの処理ではないので自前で持つ。
    """
    p = path or repo_root() / ".env"
    loaded: dict[str, str] = {}
    if not p.is_file():
        return loaded
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        loaded[key] = value
        os.environ.setdefault(key, value)
    return loaded


def credential(name: str) -> str | None:
    """認証情報を1か所から取る。未設定は None（例外にしない）。

    キーが無くても動く経路を各エンリッチャが持っており、
    「何が取れなかったか」は manifest に出る。
    """
    load_dotenv()
    value = os.environ.get(name)
    return value or None


# --------------------------------------------------------------------------
# 母集団設定
# --------------------------------------------------------------------------


class _Cfg(BaseModel):
    model_config = ConfigDict(extra="allow")


class EdinetSourceConfig(_Cfg):
    url: str
    send_subscription_key: bool = True
    subscription_key_param: str = "Subscription-Key"
    encoding: str = "cp932"
    header_row: int = 1
    columns: dict[str, int] = Field(default_factory=dict)


class MarketFilterConfig(_Cfg):
    method: str = "securities_code_presence"
    segment_source: str = "none"
    segment_allowlist: str | None = None
    #: この母集団が市場区分による絞り込みを前提としているか。
    #: true なのに segment_source が none だと、実際には全上場企業が
    #: 取れてしまうので P1 が警告を出す。
    expects_segment: bool = False


class IndustryConfig(_Cfg):
    primary_scheme: str | None = None
    common_mapping: str | None = None
    primary_scheme_by_country: dict[str, str] | None = None
    common_mapping_fallback: str | None = None


class SourceConfig(_Cfg):
    primary: str
    edinet_code_list: EdinetSourceConfig | None = None
    market_filter: MarketFilterConfig = Field(default_factory=MarketFilterConfig)
    enrich: list[str] = Field(default_factory=list)
    industry: IndustryConfig = Field(default_factory=IndustryConfig)


class RefreshConfig(_Cfg):
    cadence: str = "monthly"
    method: str = "api"
    cache_ttl_hours: int = 24


class AcceptanceConfig(_Cfg):
    expected_count_min: int | None = None
    expected_count_max: int | None = None
    max_missing_rate: dict[str, float] = Field(default_factory=dict)


class PopulationConfig(_Cfg):
    id: str
    label: str
    country: str
    enabled: bool = True
    #: そのフェーズが実装済みか。未実装の母集団は CLI が理由付きで止まる。
    implemented: bool = False
    blocked_by: str | None = None

    source: SourceConfig
    refresh: RefreshConfig = Field(default_factory=RefreshConfig)
    acceptance: AcceptanceConfig = Field(default_factory=AcceptanceConfig)
    attribution: list[str] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    dedup: dict[str, Any] = Field(default_factory=dict)

    #: 読み込み元。config_hash の計算に使う
    source_path: Path | None = None


def load_population(path: Path | str) -> PopulationConfig:
    p = config_path(str(path))
    if not p.is_file():
        raise FileNotFoundError(f"母集団設定が見つかりません: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    cfg = PopulationConfig.model_validate(raw)
    cfg.source_path = p
    return cfg


def list_populations(directory: Path | str = "configs/populations") -> list[PopulationConfig]:
    """コンソールの母集団プリセット一覧に使う。壊れた YAML は黙って飛ばさない。"""
    d = config_path(str(directory))
    out: list[PopulationConfig] = []
    for f in sorted(d.glob("*.yaml")):
        out.append(load_population(f))
    return out


# --------------------------------------------------------------------------
# 計測設定・辞書
# --------------------------------------------------------------------------


def load_yaml(path: Path | str) -> dict[str, Any]:
    p = config_path(str(path))
    if not p.is_file():
        raise FileNotFoundError(f"設定ファイルが見つかりません: {p}")
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def load_measure_config() -> dict[str, Any]:
    return load_yaml("configs/measure.yaml")


def load_selector_list(path: Path | str = "configs/dkim_selectors/l1_core.txt") -> list[str]:
    """1行1セレクタ。# 以降はコメント。重複は順序を保って除去する。"""
    p = config_path(str(path))
    seen: dict[str, None] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        token = line.split("#", 1)[0].strip()
        if token:
            seen.setdefault(token, None)
    return list(seen)
