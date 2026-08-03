"""DKIM セレクタ辞書の三層（DESIGN.md P4「DKIM の三層辞書」）。

  L1  40〜60。Wang et al. の頻出上位40相当 + 主要ESP既定。全ドメインに投げる
  L2  MX/SPF から推定した事業者の既知セレクタを動的に付与。
      MX が *.outlook.com なら selector1 / selector2 を最優先にする
  L3  Tatang 辞書（3,498語）。GPL-3.0 のコピーレフト波及の法務確認が
      済むまで無効。コンソールには「開発予定」と表示する

セレクタは DNS 上で列挙できない。「未設定」と「既知セレクタでは未検出」は
厳密に区別する（原則5）。総務省が JP ドメイン外形調査から DKIM を明示的に
除外しているのがその根拠になる。
"""

from __future__ import annotations

import re

from ..config import load_selector_list, load_yaml


class SelectorDictionary:
    """L1 と L2 を束ねたもの。L3 は無効のまま。"""

    def __init__(
        self,
        l1: list[str],
        providers: list[dict],
        *,
        layers: list[str] | None = None,
        l3_enabled: bool = False,
    ) -> None:
        self.l1 = l1
        self.providers = providers
        self.layers = layers or ["l1_core", "l2_provider"]
        self.l3_enabled = l3_enabled
        #: どのプロバイダが何回ヒットしたか。辞書の効きを見るため
        self.provider_hits: dict[str, int] = {}

    @classmethod
    def load(
        cls,
        *,
        l1_path: str = "configs/dkim_selectors/l1_core.txt",
        l2_path: str = "configs/dkim_selectors/l2_provider.yaml",
        measure_config: dict | None = None,
    ) -> SelectorDictionary:
        cfg = (measure_config or {}).get("dkim", {})
        layers = cfg.get("layers", ["l1_core", "l2_provider"])
        l1 = load_selector_list(l1_path) if "l1_core" in layers else []
        providers = (
            load_yaml(l2_path).get("providers", []) if "l2_provider" in layers else []
        )
        return cls(
            l1=l1,
            providers=providers,
            layers=layers,
            # 法務確認が済むまで L3 は無効
            l3_enabled=bool(cfg.get("l3_on_miss", False)),
        )

    def match_providers(self, mx_hosts: list[str], spf_includes: list[str]) -> list[dict]:
        """MX と SPF から事業者を推定する。"""
        hits: list[dict] = []
        for provider in self.providers:
            triggers = provider.get("triggers") or {}
            pattern = triggers.get("mx_pattern")
            if pattern and any(re.search(pattern, host, re.IGNORECASE) for host in mx_hosts):
                hits.append(provider)
                continue
            includes = [i.lower() for i in (triggers.get("spf_include") or [])]
            if includes and any(i in [s.lower() for s in spf_includes] for i in includes):
                hits.append(provider)
                continue
            mech = triggers.get("spf_mechanism_pattern")
            if mech and any(re.search(mech, s, re.IGNORECASE) for s in spf_includes):
                hits.append(provider)
        return hits

    def selectors_for(
        self, mx_hosts: list[str] | None = None, spf_includes: list[str] | None = None
    ) -> list[str]:
        """このドメインに投げるセレクタ列。

        L2 で推定した事業者のセレクタを先頭に置く。順序に意味があるのは、
        上限で打ち切られたときに当たりやすいものが残るようにするため。
        """
        ordered: dict[str, None] = {}
        for provider in self.match_providers(mx_hosts or [], spf_includes or []):
            self.provider_hits[provider.get("id", "unknown")] = (
                self.provider_hits.get(provider.get("id", "unknown"), 0) + 1
            )
            for selector in provider.get("selectors", []):
                ordered.setdefault(selector, None)
        for selector in self.l1:
            ordered.setdefault(selector, None)
        return list(ordered)

    def gateway_signing_domains(self) -> set[str]:
        """ゲートウェイ独自の署名ドメイン。

        IIJ セキュアMX の DKIM 署名ドメインは dxg.dox.jp でアライメント不可。
        これを実基盤と誤認しないよう除外リストとして持つ（DESIGN.md P6）。
        """
        out: set[str] = set()
        for provider in self.providers:
            domain = provider.get("gateway_signing_domain")
            if domain:
                out.add(str(domain).lower())
        return out

    def state(self) -> dict:
        """コンソール表示用。L3 は「開発予定」であることを明示する。"""
        return {
            "layers": self.layers,
            "l1_count": len(self.l1),
            "l2_provider_count": len(self.providers),
            "l3_enabled": self.l3_enabled,
            "l3_status": "planned",
        }
