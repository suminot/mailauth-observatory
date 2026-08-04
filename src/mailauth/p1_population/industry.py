"""業種の一次分類と共通12分類への写像。

一次分類（EDINET33 / SIC）と二次分類（共通12分類）を分離して持つのは、
原典の改定と集計軸の変更を独立に扱えるようにするため（DR-12）。
写像表そのものは CSV で Git 管理し、version を entities に記録する。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from ..paths import config_path


@dataclass(frozen=True)
class Common12:
    code: str
    label: str
    map_version: str


class IndustryMapper:
    """EDINET33 の業種ラベル -> 共通12分類。

    EDINETコードリストの「提出者業種」列は東証33業種と同一のラベル集合なので、
    ラベル文字列そのものを突合キーにする。コード列は存在しない。
    """

    def __init__(self, mapping: dict[str, Common12], map_version: str, source: Path) -> None:
        self._mapping = mapping
        self.map_version = map_version
        self.source = source
        #: 写像に無かった一次分類ラベル。辞書を育てるための材料になる
        self.unmapped: dict[str, int] = {}

    @classmethod
    def load(cls, path: str | Path) -> IndustryMapper:
        p = config_path(str(path))
        if not p.is_file():
            raise FileNotFoundError(f"業種写像が見つかりません: {p}")

        mapping: dict[str, Common12] = {}
        versions: set[str] = set()
        with p.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                label = (row.get("src_label") or "").strip()
                if not label:
                    continue
                version = (row.get("map_version") or "").strip()
                versions.add(version)
                mapping[label] = Common12(
                    code=(row.get("common_code") or "").strip(),
                    label=(row.get("common_label") or "").strip(),
                    map_version=version,
                )
        if not mapping:
            raise ValueError(f"業種写像が空です: {p}")
        if len(versions) > 1:
            raise ValueError(
                f"map_version が混在しています: {sorted(versions)}。"
                "写像を改定したら全行の version を揃えること"
            )
        return cls(mapping, versions.pop(), p)

    def map(self, primary_label: str | None) -> Common12 | None:
        """一次分類ラベルを共通12分類に写す。未知は None を返して数える。"""
        if not primary_label:
            return None
        key = primary_label.strip()
        hit = self._mapping.get(key)
        if hit is None:
            self.unmapped[key] = self.unmapped.get(key, 0) + 1
        return hit

    def top_unmapped(self, n: int = 20) -> list[tuple[str, int]]:
        return sorted(self.unmapped.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


@dataclass(frozen=True)
class SicRule:
    prefix: str
    common: Common12
    label: str


class SicMapper:
    """SIC コード -> 共通12分類。

    EDINET33 と違い SIC は**コードで引く**。写像 CSV は 2桁の一般則と
    4桁の個別則が混在しているので、**長い前置に一致するものを優先する**。
    たとえば 73（Business services）は 8 に落ちるが、7372（Prepackaged
    software）は 10 でなければならない。短い方を先に当てると、ソフトウェア
    企業が軒並みサービス業に分類される。
    """

    def __init__(
        self, rules: list[SicRule], map_version: str, source: Path
    ) -> None:
        # 長い前置から順に評価する
        self._rules = sorted(rules, key=lambda r: -len(r.prefix))
        self.map_version = map_version
        self.source = source
        self.unmapped: dict[str, int] = {}

    @classmethod
    def load(cls, path: str | Path, *, scheme: str = "SIC") -> SicMapper:
        p = config_path(str(path))
        if not p.is_file():
            raise FileNotFoundError(f"業種写像が見つかりません: {p}")

        rules: list[SicRule] = []
        versions: set[str] = set()
        with p.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if (row.get("scheme") or "").strip().upper() != scheme.upper():
                    continue
                code = (row.get("src_code") or "").strip()
                if not code:
                    continue
                version = (row.get("map_version") or "").strip()
                versions.add(version)
                rules.append(
                    SicRule(
                        prefix=code,
                        common=Common12(
                            code=(row.get("common_code") or "").strip(),
                            label=(row.get("common_label") or "").strip(),
                            map_version=version,
                        ),
                        label=(row.get("src_label") or "").strip(),
                    )
                )
        if not rules:
            raise ValueError(f"{scheme} の写像が空です: {p}")
        if len(versions) > 1:
            raise ValueError(
                f"map_version が混在しています: {sorted(versions)}。"
                "写像を改定したら全行の version を揃えること"
            )
        return cls(rules, versions.pop(), p)

    def map(self, sic: str | None) -> Common12 | None:
        """SIC コードを共通12分類に写す。未知は None を返して数える。"""
        if not sic:
            return None
        # SIC は概念上4桁で、先頭ゼロを落として3桁で流通することがある。
        # 左ゼロ詰めが SIC の慣行だが、`737` を `0737`（農業サービス）と
        # 読むか `737x`（ソフトウェア）と読むかは本来決まらない。
        # SEC は4桁で返すので、ここに来る3桁は素性の悪い入力である
        code = str(sic).strip()
        if not code.isdigit():
            self.unmapped[code] = self.unmapped.get(code, 0) + 1
            return None
        padded = code.zfill(4)
        for rule in self._rules:
            if padded.startswith(rule.prefix):
                return rule.common
        self.unmapped[padded] = self.unmapped.get(padded, 0) + 1
        return None

    def top_unmapped(self, n: int = 20) -> list[tuple[str, int]]:
        return sorted(self.unmapped.items(), key=lambda kv: (-kv[1], kv[0]))[:n]
