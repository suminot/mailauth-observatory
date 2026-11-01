// 書体をビルドの時に取ってきて、自前で配る（BACKLOG 11）。
//
// **閲覧者の IP を Google に渡さない。** これまでは Google Fonts の CSS を
// ページから直接読んでいたので、開いた人の IP とアクセス時刻が Google に
// 届いていた。`Referrer-Policy: no-referrer` を置いている意図と噛み合わない。
//
// 取りに行くのはビルドの実行環境だけにする。
//
//   src/_fonts/fonts.css        Framework がハッシュを付けて配る
//   static/_fonts/files/*.woff2 copy-static.mjs が dist/_fonts/files/ に置く
//   static/_fonts/OFL-*.txt     ライセンス文（OFL 1.1 は同梱が条件）
//
// **字形は絞らない。** 変更履歴は毎月生成されるので、来月には今日いない漢字が
// 出る。足りない字は黙って代替フォントに落ちる。Google の unicode-range の
// 断片分けをそのまま使い、ブラウザにはそのページに出る字の断片だけを取らせる。
//
// **黙って通さない。** 1つでも取れなければ非ゼロで落とす。書体が欠けたまま
// デプロイされると、画面を見ない限り気付けない（crt.sh の件と同じ形）。
//
// どちらも生成物で、リポジトリには入れない（.gitignore）。

import { mkdir, writeFile, rm } from "node:fs/promises";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";

const here = dirname(fileURLToPath(import.meta.url));
const site = join(here, "..");
const cssDir = join(site, "src", "_fonts");
const fileDir = join(site, "static", "_fonts", "files");
const licenseDir = join(site, "static", "_fonts");

// 頼む書体と太さ。**ページが実際に使うものだけ。** Source Serif 4 は
// Observable Framework の既定のテーマが使う（observablehq.config.js の
// globalStylesheets を空にして、Framework からは読ませない）
const CSS_URL =
  "https://fonts.googleapis.com/css2" +
  "?family=Chakra+Petch:wght@500;600;700" +
  "&family=Noto+Sans+JP:wght@400;500;700" +
  "&family=JetBrains+Mono:wght@400;700" +
  "&family=Source+Serif+4:ital,opsz,wght@0,8..60,200..900;1,8..60,200..900" +
  "&display=swap";

// ライセンス文の置き場所（google/fonts のリポジトリ）。4書体とも OFL 1.1
const LICENSES = {
  "Chakra Petch": "https://raw.githubusercontent.com/google/fonts/main/ofl/chakrapetch/OFL.txt",
  "Noto Sans JP": "https://raw.githubusercontent.com/google/fonts/main/ofl/notosansjp/OFL.txt",
  "JetBrains Mono": "https://raw.githubusercontent.com/google/fonts/main/ofl/jetbrainsmono/OFL.txt",
  "Source Serif 4": "https://raw.githubusercontent.com/google/fonts/main/ofl/sourceserif4/OFL.txt",
};

// woff2 を返してもらうための User-Agent。古いブラウザの UA だと ttf になる
const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/131.0 Safari/537.36";

async function get(url, as) {
  let last;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const res = await fetch(url, { headers: { "user-agent": UA } });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return as === "text" ? await res.text() : Buffer.from(await res.arrayBuffer());
    } catch (err) {
      last = err;
      await new Promise((r) => setTimeout(r, 1000 * 2 ** attempt));
    }
  }
  throw new Error(`${url} を取れなかった: ${last?.message ?? last}`);
}

function fail(message) {
  console.error(`fetch-fonts: ${message}`);
  console.error(
    "書体を自前で配れないままデプロイしない。fonts.googleapis.com / fonts.gstatic.com が" +
      "落ちていないか確かめ、戻ってから流し直すこと"
  );
  process.exit(1);
}

let css;
try {
  css = await get(CSS_URL, "text");
} catch (err) {
  fail(err.message);
}

const urls = [...new Set([...css.matchAll(/url\((https:\/\/fonts\.gstatic\.com\/[^)]+)\)/g)].map((m) => m[1]))];
if (urls.length === 0) fail("CSS に書体ファイルの参照が1つも無い（応答の形が変わった？）");

await rm(fileDir, { recursive: true, force: true });
await mkdir(fileDir, { recursive: true });
await mkdir(cssDir, { recursive: true });

// 名前は URL から決める。**同じ URL は同じ名前**になるので、ビルドを
// 繰り返しても配るファイル名が変わらない
const names = new Map(
  urls.map((u) => [u, createHash("sha256").update(u).digest("hex").slice(0, 16) + ".woff2"])
);

let bytes = 0;
const queue = [...urls];
const errors = [];
await Promise.all(
  Array.from({ length: 12 }, async () => {
    while (queue.length) {
      const url = queue.shift();
      try {
        const body = await get(url, "buffer");
        bytes += body.length;
        await writeFile(join(fileDir, names.get(url)), body);
      } catch (err) {
        errors.push(err.message);
      }
    }
  })
);
if (errors.length) fail(`${errors.length} 件の書体ファイルを取れなかった。最初: ${errors[0]}`);

const local = css.replace(/url\((https:\/\/fonts\.gstatic\.com\/[^)]+)\)/g, (_, u) => `url(/_fonts/files/${names.get(u)})`);
if (/fonts\.(googleapis|gstatic)\.com/.test(local)) fail("書き換えた CSS に Google の URL が残っている");
await writeFile(
  join(cssDir, "fonts.css"),
  "/* scripts/fetch-fonts.mjs が生成。Google Fonts の CSS の URL を自前に書き換えたもの。\n" +
    "   書体はいずれも SIL Open Font License 1.1（/_fonts/OFL-*.txt） */\n" +
    local
);

for (const [family, url] of Object.entries(LICENSES)) {
  try {
    const text = await get(url, "text");
    await writeFile(join(licenseDir, `OFL-${family.replaceAll(" ", "")}.txt`), text);
  } catch (err) {
    fail(`${family} のライセンス文: ${err.message}`);
  }
}

console.log(
  `fetch-fonts: 書体ファイル ${urls.length} 件（${(bytes / 1024 / 1024).toFixed(1)} MB）と` +
    `ライセンス文 ${Object.keys(LICENSES).length} 件を置いた`
);
