"""辞書メンテナンス（画面5）── Sprint 5 で実装。

この画面が国内ベンダー辞書を育てる装置である（DESIGN.md 7.2）。
リサーチで埋まらなかった空白は、ここでしか埋まらない。

実装時の要件
  - 未知 MX ホスト名を頻度順に表示し、ベンダーを手入力して
    configs/fingerprints/*.yaml に追記する（3クリック以内）
  - 未知 SPF include も同様
  - 追記後、その場で P6 を再実行して効果を確認できる
  - DKIM 辞書の状態表示。L1/L2 は稼働中、L3 は「開発予定」バッジ
  - 飽和曲線（セレクタ数 対 新規発見ドメイン数）

Sprint 1 では、辞書の現況を読み取り専用で返すところまでを用意する。
コンソールに L3 の状態を出すのは Sprint 1 の時点でも意味があるため。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from mailauth.config import load_measure_config, load_selector_list, load_yaml

router = APIRouter(prefix="/api/dict", tags=["dict"])


@router.get("/dkim")
def dkim_state() -> dict[str, Any]:
    """DKIM 辞書の状態。L3 は GPL-3.0 の法務確認待ちで無効。"""
    measure = load_measure_config()
    dkim = measure.get("dkim", {})
    l1 = load_selector_list("configs/dkim_selectors/l1_core.txt")
    l2 = load_yaml("configs/dkim_selectors/l2_provider.yaml")
    providers = l2.get("providers", [])
    return {
        "layers": dkim.get("layers", []),
        "l1": {"status": "active", "count": len(l1), "selectors": l1},
        "l2": {
            "status": "active",
            "provider_count": len(providers),
            "providers": [
                {"id": p.get("id"), "label": p.get("label"), "selectors": p.get("selectors", [])}
                for p in providers
            ],
        },
        "l3": {
            "status": dkim.get("l3_status", "planned"),
            "enabled": bool(dkim.get("l3_on_miss", False)),
            "count": 0,
            "note": (
                "Tatang 辞書（3,498語）は GPL-3.0。"
                "コピーレフトの波及について法務確認が済むまで無効化している"
            ),
        },
    }


@router.get("/fingerprints")
def fingerprints() -> dict[str, Any]:
    """フィンガープリント辞書の現況（読み取り専用）。"""
    out = []
    for name in ("platforms", "security_gw", "esp", "verification_txt"):
        data = load_yaml(f"configs/fingerprints/{name}.yaml")
        out.append(
            {
                "file": f"configs/fingerprints/{name}.yaml",
                "category": data.get("category"),
                "version": data.get("version"),
                "rule_count": len(data.get("rules", [])),
                "jp_rule_count": sum(
                    1 for r in data.get("rules", []) if r.get("region") == "JP"
                ),
            }
        )
    vendors = load_yaml("configs/vendors/dmarc_rua_vendors.yaml")
    out.append(
        {
            "file": "configs/vendors/dmarc_rua_vendors.yaml",
            "category": vendors.get("category"),
            "version": vendors.get("version"),
            "rule_count": len(vendors.get("rules", [])),
            "jp_rule_count": sum(1 for r in vendors.get("rules", []) if r.get("region") == "JP"),
        }
    )
    return {"dictionaries": out}
