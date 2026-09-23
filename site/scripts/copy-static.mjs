// Observable Framework が配らないファイルを dist/ に置く。
//
// Framework は「ページから参照されているファイル」しか出力しない。
// `_headers` と `robots.txt` はどのページからも参照されないので、
// src/ に置いても dist/ に現れない（**実際にそれで落とした**）。
//
// **黙って通さない。** 置けなかったら非ゼロで落とす。検索避けの設定が
// 配られないままデプロイされると、載ってから気付くことになり、
// 索引から外れるまで待つしかなくなる。

import { cp, readdir, access } from "node:fs/promises";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const from = join(here, "..", "static");
const to = join(here, "..", "dist");

const names = await readdir(from);
if (names.length === 0) {
  console.error("static/ が空。配るものが無い");
  process.exit(1);
}

for (const name of names) {
  await cp(join(from, name), join(to, name), { recursive: true });
}

// 置けたことを確かめる。cp が黙って何もしない経路に備える
for (const name of names) {
  await access(join(to, name)).catch(() => {
    console.error(`dist/${name} を置けなかった`);
    process.exit(1);
  });
}

console.log(`copy static → dist: ${names.join(", ")}`);
