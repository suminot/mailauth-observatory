"""辞書メンテナンス（画面5）。

**この画面が国内ベンダー辞書を育てる装置である**（DESIGN.md 7.2 / P6 実装メモ）。
リサーチでは NRIセキュア、ラック、富士通、NEC、ソフトバンク、大塚商会など
多数の国内ベンダーの固定ホスト名を特定できなかった。これらは実測データからの
帰納的発見でしか埋まらない。だから「未知ホストを頻度順に見て、その場で辞書に
追記し、すぐ効果を確認する」経路を用意する。

YAML への追記は**行単位の挿入で行う**。yaml.safe_dump で書き戻すと
ファイル中のコメント（判定根拠や出典、注意書き）が全部消える。辞書の
コメントは辞書そのものと同じくらい重要な情報なので失えない。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from mailauth.config import load_measure_config, load_selector_list, load_yaml
from mailauth.contracts import ConfidenceLevel, EvidenceRecordType
from mailauth.manifest import read_manifest
from mailauth.p6_infer.fingerprints import FingerprintError, load_all, load_file
from mailauth.paths import config_path, phase_dir

router = APIRouter(prefix="/api/dict", tags=["dict"])

#: 追記を許すファイル。パスを受け取ってそのまま書くと任意ファイルを
#: 書き換えられてしまう。辞書ディレクトリの中だけに限定する
EDITABLE_DIR = "configs/fingerprints"
RUA_VENDOR_FILE = "configs/vendors/dmarc_rua_vendors.yaml"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


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
    """フィンガープリント辞書の現況。"""
    out = []
    for path in sorted(config_path(EDITABLE_DIR).glob("*.yaml")):
        relative = f"{EDITABLE_DIR}/{path.name}"
        data = load_yaml(relative)
        rules = data.get("rules", [])
        out.append(
            {
                "file": relative,
                "editable": True,
                "category": data.get("category"),
                "version": data.get("version"),
                "rule_count": len(rules),
                "jp_rule_count": sum(1 for r in rules if r.get("region") == "JP"),
            }
        )
    vendors = load_yaml(RUA_VENDOR_FILE)
    out.append(
        {
            "file": RUA_VENDOR_FILE,
            "editable": False,
            "category": vendors.get("category"),
            "version": vendors.get("version"),
            "rule_count": len(vendors.get("rules", [])),
            "jp_rule_count": sum(1 for r in vendors.get("rules", []) if r.get("region") == "JP"),
        }
    )

    rule_set = load_all()
    return {
        "dictionaries": out,
        "total_rules": len(rule_set.rules),
        "version": rule_set.version,
        "record_types": [r.value for r in EvidenceRecordType],
        # 「検出されなかった＝使っていない」ではないことを画面にも出す
        "undetectable_by_dns": [
            {"vendor": u.vendor, "product": u.product} for u in rule_set.undetectable
        ],
    }


@router.get("/saturation")
def saturation(run: str = Query(..., description="実行ID")) -> dict[str, Any]:
    """DKIM セレクタの飽和曲線（DESIGN.md 7.2 画面5）。

    L1/L2 で足りているかを判断する材料。曲線が寝ていれば辞書を増やしても
    新規発見はほとんど無い。L3（GPL-3.0 で無効）を有効化する価値が
    あるかどうかもここで見る。

    bronze から計算する。**DNS は引かない。**
    """
    from mailauth.p4_measure.saturation import from_bronze
    from mailauth.paths import bronze_dir, run_dir

    if not run_dir(run).is_dir():
        raise HTTPException(status_code=404, detail=f"run {run} がありません")

    curve = from_bronze(bronze_dir(run))
    payload = curve.to_dict()
    payload["run_id"] = run
    # 辞書の現況を添える。L1 が何個あるかが分母の意味を決める
    measure = load_measure_config().get("dkim", {})
    payload["dictionary"] = {
        "layers": measure.get("layers", []),
        "l1_size": len(load_selector_list("configs/dkim_selectors/l1_core.txt")),
        "l3_status": measure.get("l3_status", "planned"),
        "l3_enabled": bool(measure.get("l3_on_miss", False)),
    }
    return payload


@router.get("/unknown-hosts")
def unknown_hosts(run: str = Query(..., description="実行ID")) -> dict[str, Any]:
    """P6 が見つけた未知 MX ホストを頻度順に返す。

    ここが辞書を育てる主要な経路になる。集約は登録ドメイン単位で、
    顧客別ホスト名は examples に入る。
    """
    manifest = read_manifest(phase_dir(run, "p6_infer"))
    if manifest is None:
        raise HTTPException(
            status_code=404,
            detail=f"{run} の P6 の実行結果がありません。先に p6-infer を実行してください",
        )
    breakdown = manifest.get("breakdown") or {}
    return {
        "run_id": run,
        "hosts": breakdown.get("unknown_mx_hosts") or [],
        "domains_with_no_inference": breakdown.get("domains_with_no_inference"),
        "no_inference_rate": breakdown.get("no_inference_rate"),
        "fingerprint_version": (manifest.get("tool_versions") or {}).get("fingerprints"),
    }


class NewRule(BaseModel):
    """画面から手入力する1件の規則。"""

    file: str = Field(description=f"{EDITABLE_DIR}/ 以下の YAML")
    id: str
    vendor: str
    record: str
    pattern: str
    product: str | None = None
    confidence: str = ConfidenceLevel.MEDIUM
    region: str | None = None
    note: str | None = None
    category: str | None = None


def _resolve_editable(name: str) -> Path:
    """追記先を辞書ディレクトリの中に限定する。"""
    candidate = Path(name)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise HTTPException(status_code=400, detail=f"追記できないパスです: {name}")
    resolved = config_path(str(candidate)).resolve()
    allowed = config_path(EDITABLE_DIR).resolve()
    if resolved.parent != allowed or resolved.suffix != ".yaml":
        raise HTTPException(
            status_code=400,
            detail=f"{EDITABLE_DIR}/*.yaml 以外には追記できません: {name}",
        )
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail=f"辞書がありません: {name}")
    return resolved


def _scalar(value: str) -> str:
    """YAML のスカラとして安全な表現にする。

    `@nifty` のように `@` で始まる値は plain scalar として書けない。
    JSON 文字列は YAML の double-quoted scalar として妥当なので流用する。
    """
    return json.dumps(value, ensure_ascii=False)


def render_rule(rule: NewRule) -> str:
    """規則1件を YAML のリスト項目にする。"""
    lines = [f"  - id: {rule.id}", f"    vendor: {_scalar(rule.vendor)}"]
    if rule.product:
        lines.append(f"    product: {_scalar(rule.product)}")
    if rule.category:
        lines.append(f"    category: {rule.category}")
    if rule.region:
        lines.append(f"    region: {rule.region}")
    lines.append("    match:")
    lines.append(f"      record: {rule.record}")
    lines.append(f"      pattern: {_scalar(rule.pattern)}")
    lines.append(f"    confidence: {rule.confidence}")
    if rule.note:
        lines.append(f"    note: {_scalar(rule.note)}")
    return "\n".join(lines) + "\n"


def insert_into_rules_block(text: str, block: str) -> str:
    """`rules:` ブロックの末尾に挿入する。

    **単純な末尾追記はできない。** security_gw.yaml のように
    `rules:` の後ろに `undetectable_by_dns:` が続くファイルがあり、
    末尾に足すとその別のキーの下に紛れ込む。
    """
    lines = text.splitlines(keepends=True)
    start = next(
        (i for i, line in enumerate(lines) if line.rstrip("\n") == "rules:"), None
    )
    if start is None:
        raise FingerprintError("rules: ブロックが見つからない")

    end = len(lines)
    for i in range(start + 1, len(lines)):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not lines[i][0].isspace():
            end = i
            break

    # 末尾の空行と、字下げされていないコメントは次のキーの見出しなので後ろに残す。
    # security_gw.yaml では undetectable_by_dns: の直前に2行の説明コメントがあり、
    # そこへ割り込むと規則がコメントの意味から切り離される
    while end > start + 1 and (
        not lines[end - 1].strip() or lines[end - 1].startswith("#")
    ):
        end -= 1

    if end > start + 1 and not lines[end - 1].endswith("\n"):
        lines[end - 1] += "\n"
    lines.insert(end, "\n" + block)
    return "".join(lines)


@router.post("/rules")
def add_rule(rule: NewRule) -> dict[str, Any]:
    """辞書に規則を1件追記する。

    書いてから検証するのではなく、**書き戻した内容を読み直して検証し、
    通らなければ元に戻す**。壊れた辞書を残すと P6 が起動しなくなる。
    """
    if not _ID_RE.match(rule.id):
        raise HTTPException(
            status_code=400,
            detail="id は英小文字・数字・ハイフンのみ（例: nri-mx-01）",
        )
    if rule.record not in {r.value for r in EvidenceRecordType}:
        raise HTTPException(status_code=400, detail=f"未知の record: {rule.record}")
    if rule.confidence not in {c.value for c in ConfidenceLevel}:
        raise HTTPException(
            status_code=400, detail=f"未知の confidence: {rule.confidence}"
        )
    try:
        re.compile(rule.pattern)
    except re.error as exc:
        raise HTTPException(
            status_code=400, detail=f"正規表現がコンパイルできません: {exc}"
        ) from exc

    existing = {r.id for r in load_all().rules}
    if rule.id in existing:
        raise HTTPException(status_code=409, detail=f"id が既にあります: {rule.id}")

    path = _resolve_editable(rule.file)
    before = path.read_text(encoding="utf-8")
    try:
        after = insert_into_rules_block(before, render_rule(rule))
    except FingerprintError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    path.write_text(after, encoding="utf-8")
    try:
        reloaded = load_file(path)
    except Exception as exc:  # noqa: BLE001 - どんな失敗でも元に戻すのが目的
        path.write_text(before, encoding="utf-8")
        raise HTTPException(
            status_code=400,
            detail=f"追記後の辞書を読み直せなかったため元に戻しました: {exc}",
        ) from exc

    return {
        "file": rule.file,
        "rule_id": rule.id,
        "rule_count": len(reloaded.rules),
        "next_step": (
            "P6 を再実行すると効果が確認できる（辞書の更新だけなら P4 の再計測は不要）"
        ),
    }
