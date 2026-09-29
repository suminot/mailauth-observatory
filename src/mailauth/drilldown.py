"""第2層（個社名付き明細）を、公開しない形で1枚の HTML に出す。

## これは公開経路ではない

`p8_publish` の第2層のゲート（`evaluate_tier2`）は**公開してよいか**を
判定する。条件は事前通知から最低30日と、アクセス制御の2つである。

ここは公開しない。**運営者が手元で見るためのもの**で、書き出し先は
Actions の成果物か手元のファイルに限る。成果物は GitHub の認証の内側に
あり、リポジトリの権限を持つ人しか取れない。

**`site/` と `gold/` には書かない。** あの2つは公開経路そのもので、
そこに置いた瞬間に「アクセス制御の内側」ではなくなる。うっかり指定
できないよう、書き出し先を検査して拒む（`_refuse_public_path`）。

## 名指しには、観測の限界を必ず添える

個社を名指しする表で一番危ないのは、**こちらが観測できなかったことを
「その企業が対策していない」と読ませてしまう**ことである（原則5）。

  - DNS で見えないセキュリティ製品がある（API 連携型）
  - 何にでも応答する DNS があると DKIM は偽陽性になる
  - 引けなかったドメインは「無い」ではなく「分からない」

だから各行に**観測できたかどうか**と**疑いの印**を持たせ、
「観測できていない」を空欄ではなく明示の値として出す。
"""

from __future__ import annotations

import datetime as dt
import html
import json
from pathlib import Path

from .contracts import UndetectableReason
from .paths import config_path, gold_root, phase_output

#: 検出できない理由の表示名。**「使っていない」ではないことが一目で
#: 分かる言い方にする。** 英数字の識別子のままだと、読む人には
#: 何かのエラーに見える
UNDETECTABLE_LABELS: dict[str, str] = {
    UndetectableReason.API_MODE_PRODUCT: "DNS に出ない製品の可能性（API 連携型）",
    UndetectableReason.SELF_HOSTED_MX: "自社運用のため製品が分からない",
    UndetectableReason.SPF_FLATTENED: "SPF が平坦化されていて読めない",
    UndetectableReason.TENANT_PLACEHOLDER_MX: "テナント登録のみ（受信は別）",
    UndetectableReason.NOT_OBSERVED: "観測できていない",
}


def site_root() -> Path:
    """公開サイトの原稿の場所。**ここに第2層を置かせない。**"""
    return config_path("site")

#: 書き出しを拒む場所。**公開経路には置かせない。**
FORBIDDEN_ROOTS = ("site", "gold")


class PublicPathRefused(ValueError):
    """公開経路に書こうとした。"""


def _refuse_public_path(out: Path) -> None:
    """公開に載る場所への書き出しを拒む。

    **「気を付ける」では足りない。** 第2層は事前通知と認証が揃うまで
    公開できないので、置き場所の間違いは規約違反に直結する。
    """
    resolved = out.resolve()
    for root_fn in (site_root, gold_root):
        try:
            root = root_fn().resolve()
        except Exception:
            continue
        if resolved == root or root in resolved.parents:
            raise PublicPathRefused(
                f"{out} は公開経路（{root}）の中にある。"
                "第2層は事前通知から30日とアクセス制御が揃うまで公開できない。"
                "成果物か手元のファイルに書くこと"
            )
    # ルートの名前でも弾く。**設定で場所を移されても同じ間違いを防ぐ**
    parts = {p.lower() for p in resolved.parts}
    if parts & set(FORBIDDEN_ROOTS) and not _is_run_dir(resolved):
        raise PublicPathRefused(
            f"{out} の道に {sorted(parts & set(FORBIDDEN_ROOTS))} が含まれている。"
            "公開経路の可能性があるため書かない"
        )


def _is_run_dir(path: Path) -> bool:
    """`data/runs/...` の下なら公開経路ではない。"""
    return "runs" in {p.lower() for p in path.parts}


def _read(run_id: str, phase: str, name: str):
    import pandas as pd

    p = phase_output(run_id, phase, name)
    if not Path(p).is_file():
        raise FileNotFoundError(
            f"{p} が無い。その工程がまだ回っていないか、run ディレクトリが復元されていない"
        )
    return pd.read_parquet(p)


def build_rows(run_id: str) -> list[dict]:
    """1ドメイン1行の明細を組む。**企業名を含む。**"""
    import pandas as pd

    entities = _read(run_id, "p1_population", "entities.parquet")
    domains = _read(run_id, "p3_domains", "domains.parquet")
    facts = _read(run_id, "p5_parse", "facts.parquet")
    try:
        inferences = _read(run_id, "p6_infer", "inferences.parquet")
    except FileNotFoundError:
        inferences = pd.DataFrame(
            columns=["domain_id", "category", "vendor", "undetectable_reason"]
        )

    ent = entities.set_index("entity_id")
    df = domains.merge(
        facts,
        on=["domain_id", "entity_id"],
        how="left",
        suffixes=("", "_fact"),
    )

    # 推察は1ドメインに複数行つく。**層ごとにまとめる**
    by_domain: dict[str, dict] = {}
    for _, r in inferences.iterrows():
        d = by_domain.setdefault(
            str(r.get("domain_id")),
            {"vendors": [], "undetectable": [], "layers": {}},
        )
        v = r.get("vendor")
        if isinstance(v, str) and v:
            d["vendors"].append(f"{r.get('category')}:{v}")
            layer = r.get("layer")
            # **代表だけを層の欄に出す。** 同じ層に2つ並べると、
            # 読む人は「どちらも使っている」と読む
            if isinstance(layer, str) and layer and r.get("is_layer_primary") is not False:
                d["layers"].setdefault(layer, []).append(v)
        u = r.get("undetectable_reason")
        if isinstance(u, str) and u:
            d["undetectable"].append(u)

    def s(v, default="—"):
        if v is None:
            return default
        if isinstance(v, float) and v != v:  # NaN
            return default
        if isinstance(v, (list, tuple)):
            return ", ".join(str(x) for x in v) or default
        text = str(v)
        return text if text and text != "nan" else default

    def tri(v):
        """真偽の3値。**観測できていないことを空欄にしない**（原則5）。"""
        if v is None or (isinstance(v, float) and v != v):
            return "不明"
        return "あり" if bool(v) else "無し"

    rows: list[dict] = []
    for _, r in df.iterrows():
        eid = str(r.get("entity_id"))
        e = ent.loc[eid] if eid in ent.index else None
        inf = by_domain.get(str(r.get("domain_id")), {})
        observed = r.get("observed")
        rows.append(
            {
                "企業名": s(e["name"]) if e is not None else "—",
                "証券コード": s(e["securities_code"]) if e is not None else "—",
                "業種": s(e["industry_label"]) if e is not None else "—",
                "ドメイン": s(r.get("domain")),
                "役割": s(r.get("domain_role")),
                "確度": s(r.get("confidence")),
                # **観測できたかどうかを先頭近くに置く。** ここが「不明」の
                # 行を「対策していない」と読ませないため
                "観測": tri(observed),
                "SPF": tri(r.get("spf_present")),
                "SPF の all": s(r.get("spf_all_qualifier")),
                "SPF 参照数超過": tri(r.get("spf_exceeds_limit")),
                "DMARC": tri(r.get("dmarc_present")),
                "DMARC p": s(r.get("dmarc_p")),
                "DMARC pct": s(r.get("dmarc_pct")),
                "実効ポリシー": s(r.get("policy_label")),
                "レポート先なしの拒否": tri(r.get("blind_enforcement")),
                "DKIM": s(r.get("dkim_status")),
                "DKIM 偽陽性の疑い": tri(r.get("dkim_wildcard_suspect")),
                "MTA-STS": s(r.get("mta_sts_mode")),
                "TLS-RPT": tri(r.get("tls_rpt_present")),
                "BIMI": tri(r.get("bimi_present")),
                "DNSSEC": tri(r.get("dnssec_signed")),
                "MX": s(r.get("mx_hosts")),
                "推察": ", ".join(inf.get("vendors", [])) or "—",
                # **受信と送信で別ベンダーのことがある。** 1つに丸めない
                "実基盤": s(inf.get("layers", {}).get("platform")),
                "受信の前段": s(inf.get("layers", {}).get("inbound_gateway")),
                "送信の前段": s(inf.get("layers", {}).get("outbound_gateway")),
                # **「DNS からは分からない」を明示する。** 空欄にすると
                # 「何も使っていない」に見える
                # **理由を日本語にする。** `api_mode_product` のままだと
                # 読む人には「何かのエラー」に見え、いちばん大事な
                # 「使っていないのではない」が伝わらない
                "検出できない理由": ", ".join(
                    UNDETECTABLE_LABELS.get(u, u)
                    for u in sorted(set(inf.get("undetectable", [])))
                )
                or "—",
            }
        )
    rows.sort(key=lambda x: (x["企業名"], x["ドメイン"]))
    return rows


#: 画面の上に必ず出す断り。**消せない形で埋め込む。**
BANNER = (
    "第2層（個社名付き明細）／社外秘。**公開してはならない。** "
    "事前通知から最低30日の訂正期間とアクセス制御が揃うまで、"
    "この内容を社外に出すことはできない。"
)

#: 読む人がまず誤るところ。**表の上に置く。**
CAVEATS = [
    "「観測」が不明の行は、**対策していないのではなく、こちらが引けなかった**。",
    "DNS に痕跡を残さないセキュリティ製品がある（API / OAuth 連携型）。"
    "「検出できない理由」欄がその印で、**「使っていない」と読んではならない**。",
    "何にでも応答する DNS では DKIM が偽陽性になる。"
    "「DKIM 偽陽性の疑い」が立っている行は確かめること。",
    "これは標準への準拠状況の観測であって、**総合的なセキュリティ評価ではない**。",
]


def render_html(run_id: str, rows: list[dict], *, generated_at: dt.datetime | None = None) -> str:
    """1枚で完結する HTML。**外部への読み込みを持たない。**

    手元で開くものなので、取り込み先を持たせない（持たせると、開いた
    人の環境から外へ通信が出る）。
    """
    now = (generated_at or dt.datetime.now(dt.UTC)).strftime("%Y-%m-%d %H:%M UTC")
    cols = list(rows[0].keys()) if rows else []
    payload = json.dumps({"columns": cols, "rows": rows}, ensure_ascii=False)
    caveats = "".join(f"<li>{html.escape(c)}</li>" for c in CAVEATS)
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow, noarchive, nosnippet">
<title>{html.escape(run_id)} 個社別明細（社外秘）</title>
<style>
  :root {{ color-scheme: light dark; --fg:#111; --bg:#fff; --line:#d5d5dc;
           --muted:#666; --warn:#7a3c00; --warnbg:#fff4e2; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --fg:#e8e8ec; --bg:#16161a; --line:#33333c;
             --muted:#9a9aa4; --warn:#ffd9a8; --warnbg:#3a2a12; }}
  }}
  body {{ margin:0; padding:1rem; background:var(--bg); color:var(--fg);
         font-family: system-ui, "Hiragino Kaku Gothic ProN", "Yu Gothic", Meiryo, sans-serif; }}
  .banner {{ background:var(--warnbg); color:var(--warn); border:2px solid currentColor;
             padding:.75rem 1rem; border-radius:.4rem; font-weight:700; margin-bottom:1rem; }}
  ul.caveats {{ color:var(--muted); font-size:.85rem; line-height:1.7; max-width:80ch; }}
  .controls {{ display:flex; gap:.75rem; flex-wrap:wrap; align-items:center; margin:1rem 0; }}
  input, select {{ padding:.4rem .5rem; font:inherit; border:1px solid var(--line);
                   border-radius:.3rem; background:var(--bg); color:var(--fg); }}
  .count {{ color:var(--muted); font-size:.85rem; }}
  .wrap {{ overflow-x:auto; border:1px solid var(--line); border-radius:.4rem; }}
  table {{ border-collapse:collapse; font-size:.8rem; white-space:nowrap; }}
  th, td {{ border-bottom:1px solid var(--line); padding:.35rem .6rem; text-align:left; }}
  th {{ position:sticky; top:0; background:var(--bg); cursor:pointer; user-select:none; }}
  tbody tr:hover {{ background:color-mix(in srgb, var(--fg) 6%, transparent); }}
  td.unknown {{ color:var(--muted); font-style:italic; }}
</style></head><body>
<div class="banner">{html.escape(BANNER).replace("**", "")}</div>
<h1>{html.escape(run_id)} 個社別明細</h1>
<p class="count">生成 {now} ／ {len(rows)} 行</p>
<ul class="caveats">{caveats}</ul>
<div class="controls">
  <input id="q" type="search" placeholder="企業名・ドメインで絞る" size="30">
  <label>観測 <select id="obs">
    <option value="">すべて</option><option>あり</option>
    <option>無し</option><option>不明</option>
  </select></label>
  <label>DMARC p <select id="pol"><option value="">すべて</option></select></label>
  <span class="count" id="n"></span>
</div>
<div class="wrap"><table><thead><tr id="head"></tr></thead><tbody id="body"></tbody></table></div>
<script id="data" type="application/json">{payload}</script>
<script>
const D = JSON.parse(document.getElementById("data").textContent);
let sortBy = null, dir = 1;
const head = document.getElementById("head");
for (const c of D.columns) {{
  const th = document.createElement("th"); th.textContent = c;
  th.onclick = () => {{ dir = sortBy === c ? -dir : 1; sortBy = c; draw(); }};
  head.appendChild(th);
}}
const pol = document.getElementById("pol");
for (const v of [...new Set(D.rows.map(r => r["DMARC p"]))].sort()) {{
  const o = document.createElement("option"); o.textContent = v; pol.appendChild(o);
}}
function draw() {{
  const q = document.getElementById("q").value.trim().toLowerCase();
  const obs = document.getElementById("obs").value;
  const p = pol.value;
  let rows = D.rows.filter(r =>
    (!q || r["企業名"].toLowerCase().includes(q)
        || r["ドメイン"].toLowerCase().includes(q)) &&
    (!obs || r["観測"] === obs) && (!p || r["DMARC p"] === p));
  if (sortBy) rows = [...rows].sort(
    (a, b) => String(a[sortBy]).localeCompare(String(b[sortBy]), "ja") * dir);
  const body = document.getElementById("body");
  body.textContent = "";
  for (const r of rows.slice(0, 3000)) {{
    const tr = document.createElement("tr");
    for (const c of D.columns) {{
      const td = document.createElement("td");
      td.textContent = r[c];
      // **「不明」を目で拾えるようにする。** 空欄と同じ見た目にしない
      if (r[c] === "不明" || r[c] === "—") td.className = "unknown";
      tr.appendChild(td);
    }}
    body.appendChild(tr);
  }}
  document.getElementById("n").textContent =
    rows.length + " 行" + (rows.length > 3000 ? "（先頭 3000 行を表示）" : "");
}}
for (const el of ["q", "obs", "pol"]) document.getElementById(el).oninput = draw;
draw();
</script>
</body></html>
"""


def write_report(run_id: str, out: Path) -> tuple[Path, int]:
    """明細を書き出す。**公開経路なら拒む。**"""
    out = Path(out)
    _refuse_public_path(out)
    rows = build_rows(run_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(run_id, rows), encoding="utf-8")
    return out, len(rows)
