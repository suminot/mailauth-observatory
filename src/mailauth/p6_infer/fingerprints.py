"""フィンガープリント辞書の読み込み（DESIGN.md P6）。

規則は `configs/fingerprints/*.yaml` に置く。**コードには1つも書かない**
（原則7）。辞書を育てるのは運用の仕事であり、そのたびにコードを触る設計に
すると育たない。

読み込み時に検証すること
  - 正規表現がコンパイルできるか。壊れた規則は黙って無視せず例外にする
  - `record` が既知の証拠種別か。誤記した規則は永久に一致しないため、
    「一致0件」と区別が付かなくなる
  - `id` が辞書全体で一意か。evidence の rule_id が指す先が曖昧になる
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import load_yaml
from ..contracts import ConfidenceLevel, EvidenceRecordType, InferenceCategory
from ..paths import config_path

#: 辞書の置き場所
FINGERPRINT_DIR = "configs/fingerprints"
#: rua 宛先ドメインからレポート処理ベンダーを推定する辞書。
#: カテゴリが dmarc_vendor なのでフィンガープリントと同じ形で扱える
RUA_VENDOR_PATH = "configs/vendors/dmarc_rua_vendors.yaml"

#: `category: verification_txt` は Inference のカテゴリではない。
#: 所有権確認 TXT は「その製品を使っている」の弱い証拠にすぎないので、
#: 一致してもベンダー本来のカテゴリに寄せる（既定は mail_platform）
VERIFICATION_CATEGORY = "verification_txt"

_KNOWN_RECORDS = {r.value for r in EvidenceRecordType}
_KNOWN_CATEGORIES = {c.value for c in InferenceCategory} | {VERIFICATION_CATEGORY}
_KNOWN_CONFIDENCE = {c.value for c in ConfidenceLevel}


class FingerprintError(ValueError):
    """辞書の記述ミス。黙って無視すると「一致0件」と区別が付かない。"""


@dataclass(frozen=True)
class Corroboration:
    """所有権確認 TXT の裏付け条件（DESIGN.md P6 stale 判定）。"""

    mx_pattern: re.Pattern[str] | None = None
    spf_includes: tuple[str, ...] = ()
    dkim_cname: re.Pattern[str] | None = None


@dataclass(frozen=True)
class Rule:
    id: str
    vendor: str
    category: str
    record: str
    pattern: re.Pattern[str]
    product: str | None = None
    confidence: str = ConfidenceLevel.MEDIUM
    region: str | None = None
    note: str | None = None
    source: str | None = None
    corroborated_by: Corroboration | None = None
    #: 読み込み元ファイルの `version`。inference に記録して再現性を確保する
    fingerprint_version: str | None = None

    def matches(self, value: str) -> bool:
        return bool(self.pattern.search(value))


@dataclass
class UndetectableProduct:
    """DNS に痕跡を残さない製品（DESIGN.md P6「API連携型製品という構造的盲点」）。

    MX を変更せず API / OAuth で動作するため、**原理的に検出できない**。
    「検出されなかった＝使っていない」ではないことを出力に明示するために、
    辞書として持ち回る。
    """

    vendor: str
    product: str | None = None


@dataclass
class RuleSet:
    rules: list[Rule] = field(default_factory=list)
    undetectable: list[UndetectableProduct] = field(default_factory=list)
    #: ファイル名 -> version。manifest に出して再現性を担保する
    versions: dict[str, str] = field(default_factory=dict)

    def by_record(self, record: str) -> list[Rule]:
        return [r for r in self.rules if r.record == record]

    @property
    def version(self) -> str:
        """辞書全体の版。各ファイルの version を連結した決定的な文字列。"""
        return ";".join(f"{name}={ver}" for name, ver in sorted(self.versions.items()))


def _corroboration(raw: dict | None) -> Corroboration | None:
    if not raw:
        return None
    mx = raw.get("mx_pattern")
    dkim = raw.get("dkim_cname")
    includes = raw.get("spf_include") or []
    return Corroboration(
        mx_pattern=re.compile(mx, re.IGNORECASE) if mx else None,
        spf_includes=tuple(str(i).strip().lower() for i in includes),
        dkim_cname=re.compile(dkim, re.IGNORECASE) if dkim else None,
    )


def _parse_rule(raw: dict, *, default_category: str, version: str, origin: str) -> Rule:
    rule_id = str(raw.get("id") or "").strip()
    if not rule_id:
        raise FingerprintError(f"{origin}: id の無い規則がある")

    match = raw.get("match") or {}
    record = str(match.get("record") or "").strip()
    if record not in _KNOWN_RECORDS:
        raise FingerprintError(
            f"{origin}: 規則 {rule_id} の record が未知の値 {record!r}。"
            f"既知は {sorted(_KNOWN_RECORDS)}"
        )

    pattern = match.get("pattern")
    if not pattern:
        raise FingerprintError(f"{origin}: 規則 {rule_id} に pattern が無い")
    try:
        compiled = re.compile(str(pattern), re.IGNORECASE)
    except re.error as exc:
        raise FingerprintError(
            f"{origin}: 規則 {rule_id} の pattern がコンパイルできない: {exc}"
        ) from exc

    category = str(raw.get("category") or default_category).strip()
    if category not in _KNOWN_CATEGORIES:
        raise FingerprintError(
            f"{origin}: 規則 {rule_id} の category が未知の値 {category!r}。"
            f"既知は {sorted(_KNOWN_CATEGORIES)}"
        )

    confidence = str(raw.get("confidence") or ConfidenceLevel.MEDIUM).strip().lower()
    if confidence not in _KNOWN_CONFIDENCE:
        raise FingerprintError(
            f"{origin}: 規則 {rule_id} の confidence が未知の値 {confidence!r}"
        )

    vendor = str(raw.get("vendor") or "").strip()
    if not vendor:
        raise FingerprintError(f"{origin}: 規則 {rule_id} に vendor が無い")

    return Rule(
        id=rule_id,
        vendor=vendor,
        category=category,
        record=record,
        pattern=compiled,
        product=(str(raw["product"]).strip() if raw.get("product") else None),
        confidence=confidence,
        region=(str(raw["region"]).strip() if raw.get("region") else None),
        note=(str(raw["note"]).strip() if raw.get("note") else None),
        source=(str(raw["source"]).strip() if raw.get("source") else None),
        corroborated_by=_corroboration(raw.get("corroborated_by")),
        fingerprint_version=version,
    )


def load_file(path: Path | str) -> RuleSet:
    raw = load_yaml(str(path))
    origin = Path(str(path)).name
    version = str(raw.get("version") or "unknown")
    default_category = str(raw.get("category") or InferenceCategory.MAIL_PLATFORM)

    rule_set = RuleSet(versions={origin: version})
    for item in raw.get("rules") or []:
        rule_set.rules.append(
            _parse_rule(
                item, default_category=default_category, version=version, origin=origin
            )
        )
    for item in raw.get("undetectable_by_dns") or []:
        rule_set.undetectable.append(
            UndetectableProduct(
                vendor=str(item.get("vendor") or "").strip(),
                product=(str(item["product"]).strip() if item.get("product") else None),
            )
        )
    return rule_set


def load_all(
    directory: Path | str = FINGERPRINT_DIR,
    *,
    rua_vendors: Path | str | None = RUA_VENDOR_PATH,
) -> RuleSet:
    """辞書をすべて読み込む。

    `id` の重複はここで弾く。evidence の rule_id が指す先が曖昧になると、
    「なぜそう推定したか」を後から追えなくなる（原則2）。
    """
    combined = RuleSet()
    paths = sorted(config_path(str(directory)).glob("*.yaml"))
    if rua_vendors:
        rua_path = config_path(str(rua_vendors))
        if rua_path.is_file():
            paths.append(rua_path)

    seen: dict[str, str] = {}
    for path in paths:
        part = load_file(path)
        for rule in part.rules:
            if rule.id in seen:
                raise FingerprintError(
                    f"規則 id が重複している: {rule.id}"
                    f"（{seen[rule.id]} と {path.name}）"
                )
            seen[rule.id] = path.name
        combined.rules.extend(part.rules)
        combined.undetectable.extend(part.undetectable)
        combined.versions.update(part.versions)
    return combined
