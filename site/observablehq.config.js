// 公開サイトの設定（DESIGN.md P8）。
//
// 表現上の規約は法務要件でもある。ここで決めているのは次の3点。
//   - 限界の明示をフッタに常時表示する（P8 が文言の一致を検査する）
//   - 総合順位や A〜F グレードのページを作らない
//   - 配色は3段階。赤の面積を最小化する（styles.css の --absent を参照）

import { execSync } from "node:child_process";

/** 何から組んだか。画面の下端に `Build #079` のように出す。
 *
 * squash マージの件名は `… (#79)` で終わるので、そこから拾う。
 *
 * **直近のコミットだけを見ない。** 月次計測のコミット（`月次計測 2026-09`）
 * は PR を経ないので番号が無く、以前はそこで短い commit に落ちていた。
 * 実際そうなり、画面に `Build e23fedc` と出た ── **読み手には何のことか
 * 分からない。** 番号が付いた直近のコミットまで遡る。
 *
 * 遡って出るのは「サイトがどの PR の状態か」で、それは正しい。データだけ
 * が新しい場合でも、ページの作りはその PR のままである。
 *
 * **3桁でゼロ詰めする。** 桁が変わると並びが揺れて、見比べたときに
 * 増えたのか減ったのか一瞬迷う。3桁を超えたらそのまま伸ばす。
 *
 * どれも取れなければ null ── 分からないなら出さない。
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
    // **遡る範囲に上限を置く。** 番号付きが1つも無いリポジトリで
    // 履歴を全部読ませない
    const subjects = run("git log -50 --pretty=%s").split("\n");
    for (const subject of subjects) {
      const pr = subject.trim().match(/\(#(\d+)\)\s*$/);
      if (pr) return `#${pr[1].padStart(3, "0")}`;
    }
    return null;
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
    // **題字からしか入れない状態にしない。** 左上のサイト名を押すと
    // ダッシュボードに戻るが、**あれが押せることは見て分からない。**
    // 一覧に項目として置く
    { name: "ダッシュボード", path: "/" },
    { name: "業種別", path: "/sectors" },
    { name: "メール基盤", path: "/platforms" },
    { name: "方法論", path: "/methodology" },
    { name: "データ", path: "/data" },
    { name: "免責事項・利用規約", path: "/terms" },
    { name: "変更履歴", path: "/changelog" },
    // 計測が一巡したときに表紙がどう見えるか。**数字は作り物**で、
    // ページ自身が冒頭でそう断っている
    { name: "サンプル", path: "/sample" },
    { name: "Dashboard", path: "/en/" },
    { name: "By sector", path: "/en/sectors" },
    { name: "Mail platforms", path: "/en/platforms" },
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
  // **検索は出さない。** 索引に入るのはページの地の文だけで、
  // 人が探すもの（ベンダー名・企業名・数字）は実行時に JSON から
  // 描いているので**一度も索引に入らない。** 実測で `IIJ` も
  // `Microsoft` も `HENNGE` も 0 件だった（`DMARC` は出る）。
  //
  // しかも日本語版で打つと英語版のページが混ざって出ていた ──
  // 一覧は言語で絞っているのに、検索だけ素通しだった。
  //
  // ページは8つで、左の一覧に常に全部出ている。**探す必要がない。**
  // 何も返ってこない入口は、読み手の手を止めるだけである
  // （#72 で意味の無いチェックボックスを外したのと同じ理由）。
  search: false,
  // 前後のページへ送るリンクは出さない。左の一覧から移動する。
  // 工程や章立てのような順序があるわけではないので、順番に読ませる導線は
  // 実際の構成と合わない
  pager: false,
};
