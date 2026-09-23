"""計測対象からの除外（DESIGN.md P8「訂正申告」）。

公開サイトは「計測対象から外す依頼には応じます」と書いている。**その約束が
実装として効いていることを検査する。** 効いていない約束は、書いていない
約束より悪い。

守らせたいこと
  1. 除外したドメインには **DNS クエリを1本も出さない**
  2. 依頼の範囲を狭く解釈しない（サブドメインも企業単位も外す）
  3. 登録簿が読めなければ**計測を進めない**
  4. 除外した件数を**必ず記録する**（黙って分母から抜かない）
"""

from __future__ import annotations

import datetime as dt

import pytest

from mailauth import exclusions
from mailauth.paths import repo_root

HEADER = ",".join(exclusions.COLUMNS) + "\n"


def _write(tmp_path, rows: str = ""):
    path = tmp_path / "excluded.csv"
    path.write_text(HEADER + rows, encoding="utf-8")
    return exclusions.load(path)


# --------------------------------------------------------------------------
# 読めなかったことを 0 件にしない
# --------------------------------------------------------------------------


def test_a_missing_registry_is_not_zero_exclusions(tmp_path):
    """**「読めなかった」を「除外の依頼が無い」にしない。**"""
    registry = exclusions.load(tmp_path / "nope.csv")
    assert registry.available is False
    assert registry.entries == []
    with pytest.raises(exclusions.ExclusionsUnavailableError) as exc:
        exclusions.require_available(registry)
    assert "除外の依頼が無い" in str(exc.value)


def test_an_empty_registry_is_distinguishable_from_a_missing_one(tmp_path):
    registry = _write(tmp_path)
    assert registry.available is True
    assert registry.entries == []
    exclusions.require_available(registry)  # 通る


def test_a_registry_without_a_target_column_is_unusable(tmp_path):
    path = tmp_path / "excluded.csv"
    path.write_text("company,note\nfoo,bar\n", encoding="utf-8")
    assert exclusions.load(path).available is False


# --------------------------------------------------------------------------
# 依頼の範囲を狭く解釈しない
# --------------------------------------------------------------------------


def test_exclusion_covers_subdomains(tmp_path):
    registry = _write(tmp_path, "example.jp,,domain,2026-08-01,C-2026-001,依頼\n")
    assert registry.excludes("example.jp") is True
    assert registry.excludes("mail.example.jp") is True
    assert registry.excludes("notexample.jp") is False


def test_entity_scoped_exclusion_covers_the_whole_company(tmp_path):
    registry = _write(tmp_path, ",jp:1234567890123,entity,2026-08-01,,全社除外\n")
    assert registry.excludes(entity_id="jp:1234567890123") is True
    assert registry.excludes("anything.jp", entity_id="jp:1234567890123") is True
    assert registry.excludes("anything.jp", entity_id="jp:9999999999999") is False
    assert registry.entity_ids == {"jp:1234567890123"}


def test_a_domain_scoped_entity_row_does_not_exclude_the_whole_company(tmp_path):
    """scope=domain の行で企業まで外さない。**依頼の範囲を広げすぎない。**"""
    registry = _write(tmp_path, "a.example.jp,jp:1,domain,2026-08-01,,\n")
    assert registry.excludes(entity_id="jp:1") is False
    assert registry.excludes("a.example.jp") is True


def test_an_entity_only_row_defaults_to_entity_scope(tmp_path):
    registry = _write(tmp_path, ",jp:1,,2026-08-01,,\n")
    assert registry.entries[0].scope == "entity"


def test_the_registry_has_no_delete_function():
    """取り消しは人が git 上で行う。コードに削除経路を持たせない。"""
    assert not hasattr(exclusions, "remove")
    assert not hasattr(exclusions, "delete")
    assert not hasattr(exclusions.ExclusionRegistry, "remove")


def test_the_registry_has_no_expiry_column():
    """**期限を設けない。** 「1年経ったからまた測る」を可能にしない。"""
    assert "expires" not in exclusions.COLUMNS
    assert "expires_on" not in exclusions.COLUMNS


def test_append_creates_the_file_with_a_header(tmp_path):
    path = tmp_path / "sub" / "excluded.csv"
    exclusions.append(
        domain="x-example.jp",
        correction_id="C-2026-009",
        requested_on=dt.date(2026, 8, 1),
        path=path,
    )
    text = path.read_text(encoding="utf-8")
    assert text.splitlines()[0] == HEADER.strip()
    registry = exclusions.load(path)
    assert registry.available is True
    assert registry.excludes("x-example.jp")
    assert registry.entries[0].correction_id == "C-2026-009"


def test_append_needs_a_target(tmp_path):
    with pytest.raises(ValueError):
        exclusions.append(path=tmp_path / "x.csv")


def test_a_bad_date_does_not_break_the_judgement(tmp_path):
    """日付が読めなくても除外の判定は効く。**判定を日付に依存させない。**"""
    registry = _write(tmp_path, "example.jp,,domain,いつか,,\n")
    assert registry.available is True
    assert registry.excludes("example.jp") is True
    assert any("日付として読めない" in n for n in registry.notes)


def test_the_entry_describes_itself_with_its_correction_id(tmp_path):
    registry = _write(tmp_path, "example.jp,,domain,2026-08-01,C-2026-001,\n")
    text = registry.matched("example.jp").describe()
    assert "example.jp" in text
    assert "2026-08-01" in text
    assert "C-2026-001" in text


# --------------------------------------------------------------------------
# 同梱物と約束
# --------------------------------------------------------------------------


def test_the_shipped_registry_is_readable_and_empty():
    """空でも読めている状態にしておく。無いと計測が止まる。"""
    registry = exclusions.load()
    assert registry.available is True
    assert registry.entries == []


def test_the_site_promises_exclusion_and_the_code_provides_it():
    """公開サイトの約束と実装が一致していること。

    訂正申告のページは内部運用のため置いていない。除外の説明は
    methodology / terms に残っており、**説明がある以上、実装も要る。**
    """
    site = repo_root() / "site" / "src"
    pages = "\n".join(
        (site / name).read_text(encoding="utf-8") for name in ("methodology.md", "terms.md")
    )
    assert "計測対象から外した" in pages
    assert (repo_root() / "configs" / "domains" / "excluded.csv").is_file()


def test_the_methodology_page_explains_the_third_state():
    """**「除外した」を「観測できなかった」と混同させない。**"""
    page = (repo_root() / "site" / "src" / "methodology.md").read_text(encoding="utf-8")
    assert "測らないと決めた" in page
    assert "計測対象から外した" in page


def test_exclusion_is_separate_from_notification_optout():
    """通知のオプトアウトと計測の除外は別物である。

    「連絡は不要だが計測は構わない」という相手がいるので同じ表に混ぜない。
    """
    from mailauth.p9_notify import optout

    assert optout.DEFAULT_PATH != exclusions.DEFAULT_PATH
    assert "除外" in (exclusions.__doc__ or "")
    assert "計測そのものをしない" in (exclusions.__doc__ or "")


# --------------------------------------------------------------------------
# パイプラインで効いていること
# --------------------------------------------------------------------------


def test_p2_does_not_generate_candidates_for_an_excluded_domain(tmp_path, monkeypatch):
    """候補にならなければ以降のどの工程にも現れない。"""
    import pandas as pd

    from mailauth.contracts import ENTITY_ARROW_SCHEMA, EntityStatus
    from mailauth.io import write_parquet
    from mailauth.p2_candidates import runner as p2
    from mailauth.paths import phase_dir
    from mailauth.resolver import StaticResolver

    out = phase_dir("2026-08", "p1_population")
    out.mkdir(parents=True, exist_ok=True)
    write_parquet(
        [
            {
                "entity_id": "E1",
                "run_id": "2026-08",
                "country": "JP",
                "population_ids": ["jp-all-listed"],
                "name": "テストA",
                "status": EntityStatus.ACTIVE,
                "official_domain": "keep-example.jp",
            },
            {
                "entity_id": "E2",
                "run_id": "2026-08",
                "country": "JP",
                "population_ids": ["jp-all-listed"],
                "name": "テストB",
                "status": EntityStatus.ACTIVE,
                "official_domain": "drop-example.jp",
            },
        ],
        out / "entities.parquet",
        ENTITY_ARROW_SCHEMA,
    )

    registry = _write(tmp_path, "drop-example.jp,,domain,2026-08-01,C-1,依頼\n")
    monkeypatch.setattr(p2, "load_exclusions", lambda *a, **k: registry)

    result = p2.run(
        "2026-08", resolver=StaticResolver(), ct_source=p2.DisabledCtSource()
    )
    frame = pd.read_parquet(
        phase_dir("2026-08", "p2_candidates") / "domain_candidates.parquet"
    )
    assert "keep-example.jp" in set(frame["domain"])
    assert "drop-example.jp" not in set(frame["domain"])
    # **黙って落とさない**
    assert any(w["code"] == "EXCLUDED_BY_REQUEST" for w in result["warnings"])
    assert result["breakdown"]["excluded"]["count"] == 1


def test_p2_stops_when_the_registry_is_unreadable(tmp_path, monkeypatch):
    """**読めないまま計測を進めない。** 外してほしい相手を測ることになる。"""
    from mailauth.contracts import ENTITY_ARROW_SCHEMA, EntityStatus
    from mailauth.io import write_parquet
    from mailauth.p2_candidates import runner as p2
    from mailauth.paths import phase_dir
    from mailauth.resolver import StaticResolver

    out = phase_dir("2026-08", "p1_population")
    out.mkdir(parents=True, exist_ok=True)
    write_parquet(
        [
            {
                "entity_id": "E1",
                "run_id": "2026-08",
                "country": "JP",
                "population_ids": ["jp-all-listed"],
                "name": "テスト",
                "status": EntityStatus.ACTIVE,
                "official_domain": "example.jp",
            }
        ],
        out / "entities.parquet",
        ENTITY_ARROW_SCHEMA,
    )

    broken = exclusions.load(tmp_path / "nope.csv")
    monkeypatch.setattr(p2, "load_exclusions", lambda *a, **k: broken)
    with pytest.raises(exclusions.ExclusionsUnavailableError):
        p2.run("2026-08", resolver=StaticResolver(), ct_source=p2.DisabledCtSource())


def test_p4_sends_no_query_for_an_excluded_domain(tmp_path, monkeypatch):
    """**ここが最後の砦。** P3 の出力が古くてもクエリを出さない。"""
    from mailauth.contracts import DOMAIN_ARROW_SCHEMA, Confidence, MeasureTier
    from mailauth.io import write_parquet
    from mailauth.p4_measure import runner as p4
    from mailauth.paths import phase_dir

    out = phase_dir("2026-08", "p3_domains")
    out.mkdir(parents=True, exist_ok=True)
    write_parquet(
        [
            {
                "domain_id": f"d:{i}",
                "entity_id": "E1",
                "run_id": "2026-08",
                "domain": domain,
                "confidence": Confidence.CONFIRMED,
                "is_measured": True,
                "measure_tier": MeasureTier.C,
            }
            for i, domain in enumerate(("keep-example.jp", "drop-example.jp"))
        ],
        out / "domains.parquet",
        DOMAIN_ARROW_SCHEMA,
    )

    registry = _write(tmp_path, "drop-example.jp,,domain,2026-08-01,C-1,\n")
    monkeypatch.setattr(p4, "load_exclusions", lambda *a, **k: registry)

    asked: list[str] = []

    class _Backend:
        version = "test"
        resolver_label = "test"

        def query(self, query):
            asked.append(query.name)
            from mailauth.resolver import make_answer

            return make_answer(query.name, query.rtype, [])

    result = p4.run("2026-08", backend=_Backend())
    assert not any("drop-example.jp" in name for name in asked), (
        "除外したドメインに DNS クエリを出している"
    )
    assert any("keep-example.jp" in name for name in asked)
    assert any(w["code"] == "EXCLUDED_BY_REQUEST" for w in result["warnings"])


def test_p8_publishes_the_exclusion_count(tmp_path, monkeypatch):
    """**黙って分母から抜かない。** 除外件数をサイトに渡す。"""
    import json
    import shutil

    import yaml

    from mailauth.p8_publish import runner as p8
    from mailauth.paths import repo_root as _repo_root

    real = _repo_root()
    root = tmp_path / "sandbox"
    (root / "configs").mkdir(parents=True)
    shutil.copytree(
        real / "site" / "src",
        root / "site" / "src",
        ignore=shutil.ignore_patterns("data", ".observablehq"),
    )
    (root / "site" / "src" / "data").mkdir(parents=True)
    shutil.copytree(real / "configs" / "corrections", root / "configs" / "corrections")
    cfg = yaml.safe_load((real / "configs" / "publish.yaml").read_text(encoding="utf-8"))
    (root / "configs" / "publish.yaml").write_text(
        yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8"
    )
    monkeypatch.setenv("MAILAUTH_ROOT", str(root))

    registry = _write(tmp_path, "drop-example.jp,,domain,2026-08-01,C-1,\n")
    monkeypatch.setattr(p8.exclusions, "load", lambda *a, **k: registry)

    p8.run("2026-08", config=str(root / "configs" / "publish.yaml"), require_month=False)
    meta = json.loads(
        (root / "site" / "src" / "data" / "meta.json").read_text(encoding="utf-8")
    )
    assert meta["excluded"]["count"] == 1
    assert meta["excluded"]["available"] is True
    assert "測らないと決めた" in meta["excluded"]["note"]
