// 公開サイトの設定（DESIGN.md P8）。
//
// 表現上の規約は法務要件でもある。ここで決めているのは次の3点。
//   - 限界の明示をフッタに常時表示する（P8 が文言の一致を検査する）
//   - 総合順位や A〜F グレードのページを作らない
//   - 配色は3段階。赤の面積を最小化する（styles.css の --absent を参照）

import { execSync } from "node:child_process";

/** 何から組んだか。画面の下端に `Build #49` のように出す。
 *
 * squash マージの件名は `… (#49)` で終わるので、そこから拾う。月次計測の
 * ように PR を経ないコミットもあるので、**取れなければ短い commit を出す。**
 * どちらも取れなければ null ── 分からないなら出さない。
 *
 * `MAILAUTH_BUILD_REF` を渡せばそれを優先する（CI から明示できるように）。
 * **受け取った値はそのまま埋めない。** HTML の属性に入るので、番号と
 * commit に出てくる文字だけに絞る。
 */
function buildRef() {
  const clean = (v) => {
    const s = String(v ?? "").trim().replace(/[^#A-Za-z0-9._/-]/g, "");
    return s ? s.slice(0, 32) : null;
  };
  const given = clean(process.env.MAILAUTH_BUILD_REF);
  if (given) return given;
  try {
    const run = (cmd) => execSync(cmd, { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] });
    const pr = run("git log -1 --pretty=%s").trim().match(/\(#(\d+)\)\s*$/);
    if (pr) return `#${pr[1]}`;
    return clean(run("git rev-parse --short HEAD"));
  } catch {
    // git が無い／リポジトリの外。**推測しない**
    return null;
  }
}

const BUILD_REF = buildRef();

export default {
  title: "Email DNS Monitor",
  // 日英の両方を並べる。**表示の絞り込みは chrome.js が行う**
  // （いま開いている言語のものだけ残す）。Framework の一覧は1つしか
  // 持てないので、両方載せたうえで隠す
  pages: [
    { name: "業種別", path: "/sectors" },
    { name: "方法論", path: "/methodology" },
    { name: "データ", path: "/data" },
    { name: "免責事項・利用規約", path: "/terms" },
    { name: "変更履歴", path: "/changelog" },
    // 計測が一巡したときに表紙がどう見えるか。**数字は作り物**で、
    // ページ自身が冒頭でそう断っている
    { name: "サンプル", path: "/sample" },
    { name: "By sector", path: "/en/sectors" },
    { name: "Methodology", path: "/en/methodology" },
    { name: "Data", path: "/en/data" },
    { name: "Terms and disclaimer", path: "/en/terms" },
    { name: "Change log", path: "/en/changelog" },
    { name: "Sample", path: "/en/sample" },
  ],
  root: "src",
  theme: "dark",
  // 右の目次は出さない。**左の一覧に、開いているページの節を畳んで見せる。**
  // 同じものが画面の両端にあると、どちらを見ればよいか毎回考えることになる
  toc: false,
  // meta robots は HTML にしか効かない。**実体は src/_headers の
  // X-Robots-Tag** で、そちらは JSON / CSV / Parquet にも届く。
  // ここに置いてあるのは二重化で、片方の設定漏れに備えている。
  head:
    '<meta name="robots" content="noindex, nofollow, noarchive, nosnippet">\n' +
    // 何から組んだか。chrome.js がこれを読んで下端に出す。
    // **取れなければ埋めない** ── 空の meta を置くと「Build 」だけが出る
    (BUILD_REF ? `<meta name="mailauth-build" content="${BUILD_REF}">\n` : "") +
    '<link rel="preconnect" href="https://fonts.googleapis.com">\n' +
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n' +
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2' +
    '?family=Chakra+Petch:wght@500;600;700' +
    '&family=Noto+Sans+JP:wght@400;500;700' +
    '&family=JetBrains+Mono:wght@400;700&display=swap">\n' +
    // **絶対パスにする。** 相対にすると /en/ のページでは
    // `/en/styles.css` を探しに行き、ビルドが「import not found」で落ちる
    '<link rel="stylesheet" href="/styles.css">\n' +
    // **表示前に配色を決める。** 後から当てると、暗い設定の人に一瞬白い
    // 画面が出る（いわゆる flash）。ここだけは同期で読み込む
    '<script src="/theme-boot.js"></script>\n' +
    '<script type="module" src="/chrome.js"></script>',
  // 限界の明示。全ページの下端に出る
  footer:
    "本サイトは標準準拠の計測であり、総合的セキュリティ評価ではない。" +
    'データは <a href="/terms">CC0</a>。',
  search: true,
  // 前後のページへ送るリンクは出さない。左の一覧から移動する。
  // 工程や章立てのような順序があるわけではないので、順番に読ませる導線は
  // 実際の構成と合わない
  pager: false,
};
