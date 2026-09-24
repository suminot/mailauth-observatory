"""法務・倫理の受け入れ基準（DESIGN.md 第9章）をコード検査で確認する。

これらはコメントで宣言するだけでは守られない。CI で機械的に落とす。
"""

from __future__ import annotations

import re

import pytest

from mailauth.paths import repo_root

#: 検査対象。テスト自身と、方針を説明している文書は除く。
SEARCH_GLOBS = [
    "src/**/*.py",
    "console/**/*.py",
    "console/frontend/src/**/*.ts*",
    "configs/**/*",
    # 公開サイトのページ。ここが外向きの文面そのものなので必ず含める
    "site/src/**/*.md",
    "site/src/**/*.js",
    "site/*.js",
]
EXCLUDE_PARTS = {"node_modules", "dist", ".venv", "__pycache__", ".observablehq"}


def _files():
    root = repo_root()
    for pattern in SEARCH_GLOBS:
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            if EXCLUDE_PARTS & set(path.parts):
                continue
            yield path


def _read(path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


def _effective_text(path) -> str:
    """コメントと docstring を除いた「実際に効く」部分だけを返す。

    「data_j.xls は使わない」と書いた方針の説明までを違反として拾ってしまうと、
    方針を明文化するほどテストが落ちるという逆立ちした状態になる。
    検査したいのは、実際に読みに行く URL やパスの値である。
    """
    text = _read(path)
    suffix = path.suffix.lower()

    if suffix == ".py":
        import ast

        try:
            tree = ast.parse(text)
        except SyntaxError:
            return text
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(
                node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
            ):
                body = getattr(node, "body", [])
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    docstrings.add(id(body[0].value))
        return "\n".join(
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
        )

    if suffix in {".yaml", ".yml"}:
        import yaml

        def scalars(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    yield str(k)
                    yield from scalars(v)
            elif isinstance(obj, list):
                for v in obj:
                    yield from scalars(v)
            elif obj is not None:
                yield str(obj)

        try:
            return "\n".join(scalars(yaml.safe_load(text)))
        except yaml.YAMLError:
            return text

    if suffix in {".ts", ".tsx"}:
        text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
        return re.sub(r"//.*", " ", text)

    # csv / txt / rq などは行頭コメントだけ落とす
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith(("#", "#+")))


#: 設定や文書で「使わない」と方針を書くこと自体は違反ではない。
#: 違反なのは実際に取りに行ける形（URL・パス）で書かれていること。
#: Python は _effective_text が docstring とコメントを落としたあとなので、
#: 文字列定数に出てきた時点で違反として扱う。
_PROSE_SUFFIXES = {".yaml", ".yml", ".md", ".csv", ".txt", ".rq"}


def _offenders(code_pattern: str, prose_pattern: str) -> list[str]:
    hits = []
    for p in _files():
        pattern = prose_pattern if p.suffix.lower() in _PROSE_SUFFIXES else code_pattern
        if re.search(pattern, _effective_text(p)):
            hits.append(str(p.relative_to(repo_root())))
    return hits


def test_jpx_data_file_is_never_referenced():
    """JPX の data_j.xls は商用二次利用が規約で禁止。非商用でも方針として使わない。"""
    offenders = _offenders(
        code_pattern=r"data_j\.xls|jpx\.co\.jp/markets/statistics-equities",
        # 文書側は、パス区切りに続く形か JPX の配布URLだけを違反とする
        prose_pattern=r"[/\\]data_j\.xls|jpx\.co\.jp/markets/statistics-equities",
    )
    assert offenders == [], f"JPX の銘柄一覧ファイルを参照している: {offenders}"


def test_fortune_com_is_never_fetched():
    """fortune.com はスクレイピングと二次利用を規約で禁止。"""
    offenders = [
        str(p.relative_to(repo_root()))
        for p in _files()
        if re.search(r"https?://[\w.]*fortune\.com", _effective_text(p))
    ]
    assert offenders == [], f"fortune.com にアクセスしうる記述がある: {offenders}"


def test_no_credentials_are_hardcoded():
    """認証情報は .env から読む。コードにも設定にも書かない。"""
    pattern = re.compile(
        r"(Subscription-Key|api[_-]?token|app[_-]?id)\s*[:=]\s*['\"][A-Za-z0-9]{16,}",
        re.IGNORECASE,
    )
    offenders = [
        str(p.relative_to(repo_root())) for p in _files() if pattern.search(_effective_text(p))
    ]
    assert offenders == [], f"認証情報がハードコードされている: {offenders}"


def test_dkim_l3_dictionary_stays_disabled():
    """Tatang 辞書は GPL-3.0。法務確認が済むまで L3 は無効のままにする。"""
    from mailauth.config import load_measure_config

    dkim = load_measure_config()["dkim"]
    assert dkim["l3_on_miss"] is False
    assert "l3_extended" not in dkim["layers"]
    assert dkim["l3_status"] == "planned"


def test_l3_selector_file_is_empty():
    from mailauth.config import load_selector_list

    assert load_selector_list("configs/dkim_selectors/l3_extended.txt") == []


def test_attribution_is_declared_for_every_enabled_population():
    """出典表記は公共データ利用規約1.0 / 政府標準利用規約2.0 の要件。"""
    from mailauth.config import list_populations

    for cfg in list_populations():
        if cfg.enabled:
            assert cfg.attribution, f"{cfg.id} に出典表記がない"


@pytest.mark.parametrize(
    "path",
    ["configs/dkim_selectors/l1_core.txt", "configs/measure.yaml"],
)
def test_configs_are_externalized_not_hardcoded(path):
    """原則7 ── 設定はコード外に出す。ファイルが実在することを確認する。"""
    assert (repo_root() / path).is_file()


def test_bronze_and_silver_are_not_committed():
    """git-scraping の履歴肥大化を避けるため data/runs/ は .gitignore に入れる。"""
    gitignore = (repo_root() / ".gitignore").read_text(encoding="utf-8")
    assert "data/runs/" in gitignore


# -- 公開サイトの表現上の規約（DESIGN.md P8） --------------------------------
#
# 断定的な語彙や順位付けは、実在企業の設定状態を名指しで公開する以上、
# 名誉毀損のリスクに直結する。文面はコードと同じ扱いで CI に通す。


def _site_pages():
    site = repo_root() / "site" / "src"
    if not site.is_dir():
        return []
    return [
        p
        for p in sorted(site.rglob("*.md"))
        if not EXCLUDE_PARTS & set(p.parts)
    ]


def test_site_pages_avoid_assertive_vocabulary():
    """「危険」「脆弱」等の断定と、A〜F グレードを使わない。"""
    from mailauth.p8_publish import vocabulary

    problems = []
    for path in _site_pages():
        for v in vocabulary.check(path.read_text(encoding="utf-8")):
            problems.append(
                f"{path.relative_to(repo_root())}: 「{v.term}」── {v.advice}"
            )
    assert problems == [], "公開ページの表現が規約に反している:\n  " + "\n  ".join(problems)


def test_site_shows_the_disclaimer_on_every_page():
    """限界の明示を常時表示する。フッタに入れて全ページに出す。"""
    from mailauth.p8_publish import vocabulary

    config = repo_root() / "site" / "observablehq.config.js"
    assert config.is_file(), "site/observablehq.config.js が無い"
    assert vocabulary.has_disclaimer(config.read_text(encoding="utf-8")), (
        f"フッタに「{vocabulary.DISCLAIMER}」が無い"
    )


def test_publish_config_keeps_tier2_disabled():
    """第2層は既定で無効。訂正期間を経ていない個社明細を出さない。"""
    from mailauth.config import load_yaml

    tier2 = load_yaml("configs/publish.yaml")["tier2"]
    assert tier2["enabled"] is False
    assert tier2["notified_on"] is None
    assert tier2["access_control_configured"] is False


def test_required_public_pages_exist():
    """DESIGN.md P8 の必須ページ。

    訂正申告と訂正履歴のページは**外していない**のではなく、**内部運用なので
    置いていない**。外部からの訂正申告を受け付けていないため、申告の受け口を
    公開しても応対できる先が無い。登録簿と審査期限の仕組みはコード側に
    残してあり（`mailauth corrections`、P8 の第2層ゲート）、第2層を外部に
    出す段になれば訂正窓口の常設が公益目的の立証材料になる（DESIGN.md 1.4）。
    そのとき公開ページを戻す。
    """
    names = {p.stem for p in _site_pages()}
    required = {
        "index",
        "methodology",
        "terms",
        "changelog",
        "data",
    }
    assert required <= names, f"必須ページが足りない: {sorted(required - names)}"


def test_site_data_is_not_committed():
    """gold を正本にする。同じ数字を二重に持つと片方だけ古くなる。"""
    gitignore = (repo_root() / ".gitignore").read_text(encoding="utf-8")
    assert "site/src/data/*.json" in gitignore
    assert "site/src/data/*.csv" in gitignore


def test_fortune_membership_is_not_reconstructed_as_a_population():
    """Fortune の順位は編集著作物。母集団として持たない。

    実測で所属リストが CC0 / CC BY-SA から500社規模で再構築できないことが
    分かっている。計測は us-all-listed で行い、リストが手に入ったら
    ビューとして重ねる（README「Fortune 500 は母集団ではなくビュー」）。
    """
    from mailauth.config import list_populations

    by_id = {c.id: c for c in list_populations()}
    for pid in ("us-fortune500", "global500"):
        assert by_id[pid].implemented is False, f"{pid} を母集団として有効にしている"
        assert "再構築できない" in (by_id[pid].blocked_by or ""), pid


def test_sec_user_agent_is_not_faked():
    """SEC は連絡先つきの User-Agent を必須としている。偽の値を埋めない。"""
    import re as _re

    offenders = [
        str(p.relative_to(repo_root()))
        for p in _files()
        if _re.search(
            r"User-Agent[\"']?\s*:\s*[\"'][^\"']*(example\.com|test@|noreply@)",
            _effective_text(p),
        )
    ]
    assert offenders == [], f"偽の連絡先を User-Agent に書いている: {offenders}"


def test_monthly_workflow_commits_even_without_changes():
    """60日自動停止対策（DESIGN.md Sprint 8）。

    GitHub Actions は60日間リポジトリに活動が無いと schedule を止める。
    データが変わらなかった月でも必ずコミットする必要がある。
    """
    workflow = repo_root() / ".github" / "workflows" / "monthly.yml"
    assert workflow.is_file(), "月次実行のワークフローが無い"
    text = workflow.read_text(encoding="utf-8")
    assert "--allow-empty" in text, "変更が無い月にコミットしない実装になっている"
    assert "schedule" in text and "cron" in text


def test_monthly_workflow_does_not_stop_at_the_first_failure():
    """途中で止めると manifest が揃わず、どこで何件落ちたか分からなくなる。"""
    text = (repo_root() / ".github" / "workflows" / "monthly.yml").read_text(
        encoding="utf-8"
    )
    assert text.count("continue-on-error: true") >= 8, (
        "工程の途中で止まる実装になっている（原則4）"
    )
    # 最終的な成否は集約レポートで判断する
    assert "--fail-on-error" in text


def test_monthly_workflow_reads_credentials_from_secrets():
    """認証情報をワークフローに直書きしない。"""
    import re as _re

    text = (repo_root() / ".github" / "workflows" / "monthly.yml").read_text(
        encoding="utf-8"
    )
    for line in text.splitlines():
        if _re.search(r"MAILAUTH_\w+:\s*\S", line):
            assert "secrets." in line, f"認証情報が直書きされている: {line.strip()}"


def test_the_changelog_is_generated_before_the_publish_gate():
    """changelog はサイトのページなので P8 の語彙検査を通る必要がある。

    P8 のあとに書き換えると、検査を通っていない文面が公開される。
    """
    text = (repo_root() / ".github/workflows/monthly.yml").read_text(encoding="utf-8")
    assert "mailauth changelog" in text
    assert text.index("mailauth changelog") < text.index("p8-publish"), (
        "changelog の生成が P8 より後になっている"
    )


def test_the_deploy_workflow_runs_the_publish_gate_first():
    """P8 を通さずにデプロイしない。

    第1層に個社特定情報が混ざっていないか、ページに断定的な語彙が無いかを
    P8 が機械的に弾いている。ビルドだけしてデプロイすると素通りする。
    """
    workflow = repo_root() / ".github" / "workflows" / "deploy.yml"
    assert workflow.is_file(), "デプロイのワークフローが無い"
    text = workflow.read_text(encoding="utf-8")
    assert "p8-publish" in text
    assert text.index("p8-publish") < text.index("pages deploy"), (
        "P8 の検査がデプロイより後になっている"
    )


def test_the_deploy_workflow_does_nothing_without_secrets():
    """認証情報を入れる前に動くと、検査前の状態が出る可能性がある。"""
    text = (repo_root() / ".github" / "workflows" / "deploy.yml").read_text(
        encoding="utf-8"
    )
    assert "ready=false" in text
    assert text.count("steps.gate.outputs.ready == 'true'") >= 4


def test_the_notification_module_has_no_sending_path():
    """**通知はコードが勝手に始めてよいものではない。**

    DESIGN.md Sprint 9 が求めるのは通知の運用であって、無人送信ではない。
    送信経路が生えたらここで落ちる。「特定電子メール」の整理も、送信の
    可否を人が判断していることに依存している。
    """
    import re as _re

    root = repo_root() / "src" / "mailauth" / "p9_notify"
    assert root.is_dir()
    for path in sorted(root.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        # SMTP を開く経路が無いこと
        assert "smtplib" not in text, f"{path.name} が smtplib を参照している"
        assert not _re.search(r"\bsend_message\b|\bsendmail\b", text), (
            f"{path.name} に送信の呼び出しがある"
        )


def test_the_notification_body_excludes_sales_content():
    """営業要素を一切排除する（DESIGN.md Sprint 9）。

    広告宣伝性がなければ特定電子メール法の「特定電子メール」には当たらないと
    整理できるが、その整理は文面が実際にそうであることに依存している。
    """
    from mailauth.p9_notify import template

    assert template.SALES_TERMS, "営業要素の検査語が空になっている"
    body = "SPF の設定についてご提案があります"
    assert template.check_sales_terms(body), "営業要素の検査が効いていない"


def test_the_notification_url_must_be_on_the_senders_own_domain():
    """検証可能な正規ドメイン上の URL を必ず含める（DESIGN.md Sprint 9）。

    日本市場では「不審メール扱いされるリスク」が最初の障壁である。
    差出人と本文の URL のドメインが違えば、それ自体が不審メールの特徴になる。
    """
    from mailauth.p9_notify import template

    findings = [template.Finding(code="x", statement="SPF レコードを観測しました")]
    for bad in ("http://obs.example.org/x", "https://elsewhere.test/x"):
        try:
            template.render(
                "target-example.jp",
                findings,
                sender_domain="obs.example.org",
                detail_url=bad,
                method_url="https://obs.example.org/method",
                correction_contact="c@obs.example.org",
                measured_month="2026-08",
            )
        except template.TemplateError:
            continue
        raise AssertionError(f"{bad} が通ってしまった")


def test_the_optout_registry_is_permanent():
    """**断られた相手は恒久的に対象外にする。**

    「1年経ったからまた送る」を可能にする列を作ると、いつか誰かが使う。
    """
    from mailauth.p9_notify import optout

    assert "expires" not in optout.COLUMNS
    assert not hasattr(optout, "remove")


def test_notification_is_disabled_in_the_shipped_config():
    """同梱の設定では通知が動かない（第2層と同じ考え方）。"""
    import yaml

    raw = yaml.safe_load(
        (repo_root() / "configs" / "publish.yaml").read_text(encoding="utf-8")
    )
    notify = raw.get("notify") or {}
    assert notify.get("sender_domain") is None
    assert notify.get("detail_url") is None
    assert int(notify.get("correction_days") or 0) >= 30


def test_the_correction_window_is_at_least_thirty_days_everywhere():
    """事前通知後、最低30日（推奨60日）の訂正期間（DESIGN.md P8）。"""
    from mailauth.p9_notify import template

    assert template.CORRECTION_DAYS_MINIMUM == 30
    assert template.CORRECTION_DAYS_RECOMMENDED == 60


def test_the_exclusion_promise_is_machine_enforced():
    """公開サイトが「計測対象から外す依頼には応じます」と書いている。

    **効いていない約束は、書いていない約束より悪い。** 除外リストが
    P2 / P3 / P4 のそれぞれで参照されていることを確かめる。
    """
    src = repo_root() / "src" / "mailauth"
    for phase in ("p2_candidates", "p3_domains", "p4_measure"):
        text = (src / phase / "runner.py").read_text(encoding="utf-8")
        assert "load_exclusions" in text, f"{phase} が除外リストを読んでいない"
        assert "require_available" in text, (
            f"{phase} が読めない除外リストで止まらない"
        )


def test_exclusion_and_notification_optout_are_separate_registries():
    """「連絡は不要だが計測は構わない」を潰さない。"""
    from mailauth import exclusions
    from mailauth.p9_notify import optout

    assert exclusions.DEFAULT_PATH != optout.DEFAULT_PATH
    for path in (exclusions.DEFAULT_PATH, optout.DEFAULT_PATH):
        assert (repo_root() / path).is_file(), f"{path} が無い"


def test_neither_registry_can_expire_a_request():
    """断りにも除外にも期限を設けない。**恒久的に扱う。**"""
    from mailauth import exclusions
    from mailauth.p9_notify import optout

    for columns in (exclusions.COLUMNS, optout.COLUMNS):
        assert not any("expire" in c for c in columns)


def _documented_columns() -> list[str]:
    """data.md の「列の意味」表の1列目に書いてある列名を拾う。"""
    import re as _re

    text = (repo_root() / "site" / "src" / "data.md").read_text(encoding="utf-8")
    out: list[str] = []
    for line in text.splitlines():
        m = _re.match(r"^\|\s*`([a-z0-9_]+)`\s*\|", line)
        if m:
            out.append(m.group(1))
    return out


def test_the_documented_columns_exist_in_the_schema():
    """公開ページの列の説明がスキーマと食い違わないこと。

    **列名を変えたら、その表は読み手に対する嘘になる。** ダウンロードした
    CSV に無い列の説明を読ませることになるので、機械で縛る。
    """
    from mailauth.contracts import (
        STATS_BY_SECTOR_ARROW_SCHEMA,
        STATS_OVERALL_ARROW_SCHEMA,
    )

    known = {f.name for f in STATS_OVERALL_ARROW_SCHEMA} | {
        f.name for f in STATS_BY_SECTOR_ARROW_SCHEMA
    }
    documented = _documented_columns()
    assert documented, "data.md の列の表を読めていない"
    missing = [c for c in documented if c not in known]
    assert not missing, f"data.md が実在しない列を説明している: {missing}"


def test_the_download_links_point_at_files_p8_writes():
    """**壊れたダウンロードリンクを公開しない。**

    `site/src/data/` は P8 の生成物でコミットしないため、サイトのビルド時の
    リンク検証では捕まらない。ここで縛る。
    """
    import re as _re

    text = (repo_root() / "site" / "src" / "data.md").read_text(encoding="utf-8")
    linked = set(_re.findall(r'href="data/([A-Za-z0-9_.]+)"', text))
    assert linked, "data.md のダウンロードリンクを読めていない"

    # P8 が書くもの（p8_publish.runner._write_site_data）
    written = {"meta.json"}
    for name in ("stats_overall", "stats_by_sector"):
        for ext in ("json", "csv"):
            written.add(f"{name}.{ext}")

    unknown = sorted(linked - written)
    assert not unknown, f"P8 が書かないファイルへのリンクがある: {unknown}"


def test_the_published_formats_cover_the_download_links():
    """publish.yaml の formats を狭めたらリンクが壊れることを検知する。"""
    import yaml

    cfg = yaml.safe_load(
        (repo_root() / "configs" / "publish.yaml").read_text(encoding="utf-8")
    )
    formats = set((cfg.get("tier1") or {}).get("formats") or [])
    assert {"json", "csv"} <= formats, (
        "data.md が json と csv のリンクを出しているので formats から外せない"
    )


def test_committed_gold_has_a_plausible_population_size():
    """**gold は公開データセット（CC0）である。** 開発中の実行結果を
    コミットしてしまうと、公開している時系列に嘘の月が混ざる。

    実際に混ざりかけた。フィクスチャの17社に実ドメインを差した検証用の
    実行結果が `jp-all-listed`（実際は約3,830社）として gold に入り、
    `git add -A` で拾われた。**総数を見れば機械で弾ける。**

    件数の範囲は母集団の設定（acceptance）から取る。設定を持たない
    母集団は検査しない（下限を勝手に決めない）。
    """
    import re as _re

    import yaml

    from mailauth.io import read_parquet

    gold = repo_root() / "gold"
    months = sorted(
        d for d in gold.iterdir() if d.is_dir() and _re.match(r"^month=\d{4}-\d{2}$", d.name)
    ) if gold.is_dir() else []
    if not months:
        pytest.skip("コミットされた gold がまだ無い")

    ranges: dict[str, tuple[int, int]] = {}
    for path in sorted((repo_root() / "configs" / "populations").glob("*.yaml")):
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        acceptance = cfg.get("acceptance") or {}
        low, high = acceptance.get("expected_count_min"), acceptance.get("expected_count_max")
        if cfg.get("id") and low and high:
            ranges[str(cfg["id"])] = (int(low), int(high))

    problems: list[str] = []
    for month in months:
        frame = read_parquet(month / "stats_overall.parquet")
        if frame is None:
            continue
        for row in frame.to_dict(orient="records"):
            population = str(row.get("population_id") or "")
            expected = ranges.get(population)
            if not expected:
                continue
            n = int(row.get("total_entities") or 0)
            if not expected[0] <= n <= expected[1]:
                problems.append(
                    f"{month.name} の {population} が {n} 社（想定 "
                    f"{expected[0]}〜{expected[1]}）。開発中の実行結果では？"
                )
    assert not problems, "\n".join(problems)


def test_the_data_license_is_declared_consistently():
    """**公開データのライセンスは1箇所を変えて済む話ではない。**

    `publish.yaml` を変えてもサイトの文面と LICENSE-DATA が古いままなら、
    受け取った人はどちらを信じればよいか分からない。CC0 は引き継ぎ可能性の
    ための選択なので、権利付与が曖昧になると目的そのものが崩れる。

    出力データのライセンスは CC0 1.0（DESIGN.md 第11章で決定済み）。
    """
    import yaml

    declared = (
        yaml.safe_load(
            (repo_root() / "configs" / "publish.yaml").read_text(encoding="utf-8")
        ).get("tier1")
        or {}
    ).get("license")
    assert declared == "CC0-1.0", f"publish.yaml の宣言が CC0-1.0 でない: {declared}"

    # データ用のライセンスファイルが実在し、CC0 の正文を含むこと
    data_license = repo_root() / "LICENSE-DATA"
    assert data_license.is_file(), "LICENSE-DATA が無い（権利付与を明示していない）"
    text = data_license.read_text(encoding="utf-8")
    assert "CC0 1.0 Universal" in text
    # 正文が入っていること（リンクだけでは権利付与にならない）
    assert "Statement of Purpose" in text
    # **コードとデータの境界を明示する。** LICENSE は Apache-2.0
    assert "Apache-2.0" in text
    assert (repo_root() / "LICENSE").is_file()

    # OpenINTEL を混ぜると継承条項が波及しうる、という注意が残っていること
    assert "OpenINTEL" in text

    # サイトと gold の README が同じことを言っていること
    for path in ("site/src/terms.md", "gold/README.md"):
        page = (repo_root() / path).read_text(encoding="utf-8")
        assert "CC0" in page, f"{path} がライセンスに触れていない"


def test_the_design_records_the_license_decision():
    """判断待ちのまま公開データのライセンスが確定している状態を作らない。"""
    design = (repo_root() / "DESIGN.md").read_text(encoding="utf-8")
    section = design.split("## 11. 未解決事項")[1]
    open_table = section.split("### 決定済み")[0]
    assert "出力データのライセンス" not in open_table, (
        "DESIGN の未解決事項に残っている。決定したなら決定済みへ移すこと"
    )
    assert "CC0 1.0。" in section


def test_the_owner_runbook_references_things_that_exist():
    """OWNER-TASKS.md が実在しないパスや secret 名を指していないこと。

    **運営者が読んで手を動かす文書なので、古くなると作業が止まる。**
    ファイルを移したときにここで落ちる。
    """
    import re as _re

    runbook = repo_root() / "OWNER-TASKS.md"
    assert runbook.is_file(), "OWNER-TASKS.md が無い"
    text = runbook.read_text(encoding="utf-8")

    # バッククォートで囲まれた configs/ 以下のパスは実在すること
    missing = [
        p
        for p in sorted(set(_re.findall(r"`(configs/[A-Za-z0-9_./-]+)`", text)))
        if not (repo_root() / p).exists()
    ]
    assert not missing, f"実在しないパスを指している: {missing}"

    # 挙げている secret / 環境変数の名前が、実際にどこかで読まれていること。
    # **誰も読まない名前を登録させると、運営者の作業がまるごと無駄になる。**
    # ワークフローが `secrets.X` として読むか、コードが環境変数として読むか、
    # どちらかであればよい（前者はワークフロー用、後者は手元で流す用）
    workflows = "".join(
        p.read_text(encoding="utf-8")
        for p in sorted((repo_root() / ".github" / "workflows").glob("*.yml"))
    )
    sources = "".join(
        p.read_text(encoding="utf-8") for p in sorted((repo_root() / "src").rglob("*.py"))
    )
    declared = set(_re.findall(r"`((?:CLOUDFLARE|R2|BACKUP|MAILAUTH)_[A-Z0-9_]+)`", text))
    unused = sorted(
        s for s in declared if f"secrets.{s}" not in workflows and f'"{s}"' not in sources
    )
    assert not unused, f"どこからも読まれていない名前を挙げている: {unused}"

    # 挙げている CLI サブコマンドが実在すること
    from mailauth.cli import app

    names = {
        c.name for c in getattr(app, "registered_commands", []) if getattr(c, "name", None)
    }
    referenced = set(_re.findall(r"mailauth ([a-z0-9-]+)", text))
    unknown = sorted(referenced - names - {"p1-population"})
    assert not unknown, f"存在しないサブコマンドを挙げている: {unknown}"


def test_the_public_site_does_not_expose_the_repository_url():
    """公開サイトにリポジトリの URL を出さない。

    **内部運用のリポジトリなので、URL そのものを公開したくない。**
    「コードはここ」と書いてリンクを張るのは透明性としては筋がいいが、
    それは公開リポジトリを前提にした話で、ここでは前提が違う。

    生成物（site/dist）ではなく**ソース側**を検査する。dist はビルドの
    たびに作り直され、依存パッケージの package.json 由来の URL も混ざる
    ため、自分が書いた文面だけを対象にする。
    """
    site_src = repo_root() / "site" / "src"
    targets = [
        *site_src.glob("*.md"),
        *(site_src / "components").glob("*.js"),
        repo_root() / "site" / "observablehq.config.js",
    ]
    assert targets, "検査対象が1つも無い"

    problems: list[str] = []
    for path in targets:
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if "github.com" in line.lower():
                problems.append(f"{path.name}:{i}: {line.strip()[:80]}")
    assert not problems, "公開サイトにリポジトリの URL がある:\n  " + "\n  ".join(problems)


#: 公開リポジトリに出してはいけない、運営者を特定する文字列（base64）。
#:
#: **ここに平文で書くと、この検査ファイル自体が公開の置き場所になる。**
#: 消そうとしている文字列を、消すための検査に書き込んでしまっては意味が
#: 無い。GitHub の検索にも引っかかる。隠しているのではなく、**この1ファイル
#: だけ例外にしないため**に符号化している（例外にすると、検査が自分自身を
#: 見逃す構造になる）。
#:
#: 2026-09-24 に public にした際、`DESIGN.md` の本文に漢字の実名が、
#: `research-dossier.html.html` に「〜さん」呼びが入っていた。どちらも
#: 削除した。
#:
#: **運営者が出すと決めたものは、この一覧に入れない。** 現時点では
#: `pyproject.toml` の `authors`（ローマ字表記）と、
#: `configs/publish.yaml` の Cloudflare Access 用メールドメインの2つ。
#: ここで止めるのは、**文書の地の文に紛れ込んだもの**である。
#:
#: GitHub のアカウント名は**意図的に入れていない。** public にした時点で
#: URL そのものが公開されるので、User-Agent から消しても意味が無く、消すと
#: 問い合わせ先が無くなる（crt.sh への礼儀として残している）。
_OWNER_IDENTIFYING_B64 = (
    "6ZqF6YeO",
    "bWtpLmNvLmpw",
    "5LiJ5LqV",
    "TWl0c3Vp",
)


def owner_identifying_strings() -> list[str]:
    import base64 as _b64

    return [_b64.b64decode(s).decode() for s in _OWNER_IDENTIFYING_B64]


#: 運営者の名前の検査から外すもの。
#:
#: **このシステムは国内上場企業を計測する。** 一覧に挙げた社名は、
#: そのまま計測対象でもある。計測データや手動辞書に社名が出るのは
#: 当たり前であって、運営者の所属とは何の関係も無い。そこで落とすと、
#: 検査が「正しい仕事をした結果」に対して毎回鳴ることになり、やがて
#: 誰も見なくなる。
#:
#: （この説明文に社名を例として書いたら、この検査自身に捕まった。
#: 効いていることの確認にはなった）
#:
#: 止めたいのは**文書の地の文に紛れ込んだもの**なので、データは見ない。
_DATA_OR_NOISE = (
    "configs/domains/",  # 手動のドメイン辞書。社名が入る
    "configs/worklist/",  # 同定の作業リスト
    "configs/industry/",  # 業種分類の対応表
    "gold/",  # 集計結果
    "tests/fixtures/",  # 実データを模したもの
)


def _is_data_or_noise(rel: str) -> bool:
    # package-lock.json の integrity は base64 なので偶然一致する
    if rel.endswith(".lock") or rel.endswith("package-lock.json"):
        return True
    # CSV / Parquet は計測の中身。社名が入って当然
    if rel.endswith((".csv", ".parquet", ".tsv")):
        return True
    return rel.startswith(_DATA_OR_NOISE)


def test_the_repository_does_not_name_its_operator():
    """**公開リポジトリに運営者の実名と所属を残さない。**

    一度 public にすると取り返しがつかない（誰かの手元に clone が残る）。
    書き戻しはレビューで見落とされやすいので、検査で止める。

    所属を示すものを特に警戒する。実名は本人の判断で出せるが、
    **勤務先が結びつくと本人の判断の範囲を超える。**
    """
    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files"], cwd=repo_root(), capture_output=True, text=True, check=True
    ).stdout.split()
    needles = owner_identifying_strings()

    hits: list[str] = []
    for rel in tracked:
        if _is_data_or_noise(rel):
            continue
        path = repo_root() / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        hits += [f"{rel}: {n}" for n in needles if n in text]

    assert not hits, "運営者を特定する文字列が入っている:\n  " + "\n  ".join(hits)


def test_the_operator_check_actually_matches_something():
    """符号化を間違えると、**何も探さない検査**が静かに通り続ける。"""
    needles = owner_identifying_strings()
    assert len(needles) == len(_OWNER_IDENTIFYING_B64)
    assert all(n and n.strip() == n for n in needles)
    # 実在の文字列であることを、復号した値だけで確かめる
    assert any(len(n) == 2 for n in needles), "日本語の姓が入っていない"
    assert any("@" not in n and "." in n for n in needles), "ドメインが入っていない"


#: Observable Framework が自前で持っている部品のクラス名。
#:
#: **ここと同じ名前を styles.css で使うと、向こうの装飾が乗ってくる。**
#: 実際に `.note` でぶつかった。Framework の `.note` は ::before で
#: 「Note」という文字を差し込み、枠と 1rem 2rem の余白まで付けるので、
#: 数値盤の小さな注記が、いきなり注記ボックスとして描かれた。
#: 名前を変えるだけで直るが、**気付くのは画面を見たときだけ**である。
FRAMEWORK_COMPONENT_CLASSES = frozenset(
    {"note", "tip", "warning", "caution", "card", "grid", "observablehq"}
)


def _site_css() -> str:
    return (repo_root() / "site" / "src" / "styles.css").read_text(encoding="utf-8")


def test_the_site_css_does_not_reuse_framework_component_class_names():
    import re as _re

    # `.name {` や `.name,` の形で自前に定義しているクラスを拾う。
    # 子孫セレクタ（`.readout .label`）の右側も対象にする
    defined = set(_re.findall(r"\.([a-z][a-z0-9-]*)\b", _site_css()))
    clash = sorted(defined & FRAMEWORK_COMPONENT_CLASSES)
    assert not clash, (
        f"Observable Framework の部品と同じクラス名を使っている: {clash}。"
        "向こうの装飾が乗るので別の名前にすること"
    )


#: 合格・要改善・未対応の3段階に使う色。**計器の意匠に流用しない。**
EVALUATIVE_COLOR_VARS = ("--pass", "--attention", "--absent")


def test_the_coverage_ring_does_not_use_evaluative_colors():
    """**リングが緑や赤に変わると、見た人はそれを成績として読む。**

    観測できた割合は「この計測がどこまで届いたか」であって達成度ではない。
    色で評価に見せると、DESIGN.md P8 が禁じている総合グレードの表示と
    実質同じものになる。リングは読み取り色1色で描く。
    """
    import re as _re

    gauge = (repo_root() / "site" / "src" / "components" / "gauge.js").read_text(encoding="utf-8")
    # コメントは対象外。**書いてはいけない理由の説明まで弾かない**
    code = _re.sub(r"//.*", "", gauge)
    used = [v for v in EVALUATIVE_COLOR_VARS if v in code]
    assert not used, f"リングに意味色を使っている: {used}"

    # CSS 側（.coverage 配下）も同じ
    block = _re.search(r"\.coverage\b.*?(?=\n/\* -|\Z)", _site_css(), _re.S)
    assert block, ".coverage の定義が見つからない"
    used = [v for v in EVALUATIVE_COLOR_VARS if v in block.group(0)]
    assert not used, f".coverage に意味色を使っている: {used}"


def test_the_search_shortcut_label_matches_a_real_handler():
    """**押しても何も起きないショートカットを表示しない。**

    Observable Framework は検索欄の脇に Mac 以外で `Alt-K` と出すが、
    向こうの keydown は `e.metaKey && !e.altKey` という条件で、
    **Alt を明示的に除外している。** 拾われるのは ⌘K（Mac）と `/` だけで、
    表示どおりに Alt-K を押しても何も起きない。`chrome.js` で補っている。

    Framework を上げたときに向こうが直せば二重に効くが、
    **どちらも同じ動作（検索欄に合わせる）なので実害は無い。**
    逆にこちらを消すと、また何も起きないラベルに戻る。
    """
    chrome = (repo_root() / "site" / "src" / "chrome.js").read_text(encoding="utf-8")
    assert "altKey" in chrome, "Alt キーを見ていない"
    assert "KeyK" in chrome, "K を見ていない"
    assert "focus()" in chrome, "検索欄に合わせていない"


def test_the_site_shows_one_navigation_not_two():
    """右の目次は出さない。**左の一覧に節を畳んで出している。**

    同じ行き先が画面の両端にあると、どちらを見ればよいかを読むたびに
    考えることになる。`chrome.js` が左に節を足しているので、
    Framework の目次を戻すと二重になる。
    """
    config = (repo_root() / "site" / "observablehq.config.js").read_text(encoding="utf-8")
    assert "toc: false" in config, "右の目次が有効になっている"

    chrome = (repo_root() / "site" / "src" / "chrome.js").read_text(encoding="utf-8")
    assert "section-links" in chrome, "左の一覧に節を出す処理が無い"


#: 配色ごとに定義しなければならない色。**片方に無いと、その配色で
#: 前の配色の値が残る**（暗い地の文字色が白いまま明るい地に出る等）。
PALETTE_TOKENS = (
    "--bg", "--bg-deep", "--panel", "--panel-alt", "--rule", "--rule-soft",
    "--ink", "--ink-strong", "--dim", "--faint",
    "--accent", "--accent-dim", "--accent-bg", "--accent-glow",
    "--pass", "--attention", "--absent", "--neutral",
)


def test_both_color_schemes_define_the_whole_palette():
    """**片方の配色で色が1つ抜けると、そこだけ前の配色の値が残る。**

    暗い地のための文字色が明るい地に出ると読めない。見落としやすく、
    しかも「その配色で開いた人」にしか見えない。
    """
    import re as _re

    css = (repo_root() / "site" / "src" / "styles.css").read_text(encoding="utf-8")

    def tokens_in(selector: str) -> set[str]:
        # **同じセレクタの塊が複数ある。** 最初の1つだけ見ると、
        # `color-scheme` だけの塊を拾って「色が全部無い」と誤検出する
        blocks = _re.findall(_re.escape(selector) + r"\s*\{(.*?)\}", css, _re.S)
        assert blocks, f"{selector} の定義が見つからない"
        found: set[str] = set()
        for body in blocks:
            found |= set(_re.findall(r"(--[a-z0-9-]+)\s*:", body))
        return found

    # 引数に `{` を含めない。**正規表現側で足している**
    dark = tokens_in(":root")
    light_attr = tokens_in(':root[data-theme="light"]')
    missing = [t for t in PALETTE_TOKENS if t not in dark]
    assert not missing, f"暗い配色に無い色がある: {missing}"
    missing = [t for t in PALETTE_TOKENS if t not in light_attr]
    assert not missing, f"明るい配色に無い色がある: {missing}"

    # 端末の設定で明るい配色になる経路も同じ色を持つこと。
    # **属性を付けた場合だけ直して、こちらを忘れる**のが起きやすい
    assert ":root:not([data-theme=\"dark\"])" in css, (
        "端末の設定が明るいときの指定が無い。"
        "属性で選んだ場合しか効かないと、何も選んでいない人に届かない"
    )
