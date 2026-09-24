// 公開サイトの設定（DESIGN.md P8）。
//
// 表現上の規約は法務要件でもある。ここで決めているのは次の3点。
//   - 限界の明示をフッタに常時表示する（P8 が文言の一致を検査する）
//   - 総合順位や A〜F グレードのページを作らない
//   - 配色は3段階。赤の面積を最小化する（styles.css の --absent を参照）

export default {
  title: "Email DNS Monitor",
  pages: [
    { name: "業種別", path: "/sectors" },
    { name: "方法論", path: "/methodology" },
    { name: "データ", path: "/data" },
    { name: "免責事項・利用規約", path: "/terms" },
    { name: "変更履歴", path: "/changelog" },
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
    '<link rel="preconnect" href="https://fonts.googleapis.com">\n' +
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n' +
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2' +
    '?family=Chakra+Petch:wght@500;600;700' +
    '&family=Noto+Sans+JP:wght@400;500;700' +
    '&family=JetBrains+Mono:wght@400;700&display=swap">\n' +
    '<link rel="stylesheet" href="./styles.css">\n' +
    // **表示前に配色を決める。** 後から当てると、暗い設定の人に一瞬白い
    // 画面が出る（いわゆる flash）。ここだけは同期で読み込む
    '<script src="./theme-boot.js"></script>\n' +
    '<script type="module" src="./chrome.js"></script>',
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
