// 公開サイトの設定（DESIGN.md P8）。
//
// 表現上の規約は法務要件でもある。ここで決めているのは次の3点。
//   - 限界の明示をフッタに常時表示する（P8 が文言の一致を検査する）
//   - 総合順位や A〜F グレードのページを作らない
//   - 配色は3段階。赤の面積を最小化する（styles.css の --absent を参照）

export default {
  title: "メール認証月次計測",
  pages: [
    { name: "業種別", path: "/sectors" },
    { name: "方法論", path: "/methodology" },
    { name: "データ", path: "/data" },
    { name: "訂正申告", path: "/corrections" },
    { name: "訂正履歴", path: "/corrections-log" },
    { name: "免責事項・利用規約", path: "/terms" },
    { name: "変更履歴", path: "/changelog" },
  ],
  root: "src",
  theme: "light",
  // meta robots は HTML にしか効かない。**実体は src/_headers の
  // X-Robots-Tag** で、そちらは JSON / CSV / Parquet にも届く。
  // ここに置いてあるのは二重化で、片方の設定漏れに備えている。
  head:
    '<meta name="robots" content="noindex, nofollow, noarchive, nosnippet">\n' +
    '<link rel="stylesheet" href="./styles.css">',
  // 限界の明示。全ページの下端に出る
  footer:
    "本サイトは標準準拠の計測であり、総合的セキュリティ評価ではない。" +
    'データは <a href="/terms">CC0</a>。' +
    '訂正の申告は <a href="/corrections">こちら</a>。',
  search: true,
};
