// 公開サイトを**実際に開いて、実際に押す**検査。
//
// ## なぜ要るか
//
// iPhone で左下の切り替えが押せなかった不具合は、要素の存在・属性・
// CSS 変数を全部確認したうえで見逃した。**押していなかったから**である。
//
// 文字列の検査（`test_compliance.py`）は `z-index` を消せば落ちるが、
// Observable Framework が構造を変えたら**文字列は残ったまま挙動だけ壊れる。**
// 最後の一段は、本物の画面でしか確かめられない。
//
// ## 何を見るか
//
//   - 左下の切り替えが**指で押せる**（画面外・他の要素の下敷きでない）
//   - 押すと配色が変わり、言語は対応するページに遷移する
//   - Alt-K で検索欄に合う
//   - 横にはみ出していない
//   - コンソールにエラーが出ていない
//
// ## 使い方
//
//     node site/scripts/check-browser.mjs            # site/dist を見る
//     node site/scripts/check-browser.mjs --dir path
//
// 終了コードで成否を返す。落ちた項目はすべて出してから落とす
// （1件目で止めると、直すたびに開き直すことになる）。

import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { extname, join, normalize } from "node:path";
import process from "node:process";

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".woff2": "font/woff2",
};

/** dist をそのまま配る。**拡張子なしの道も .html に回す**（本番と同じ形）。 */
function serve(root) {
  const server = createServer(async (req, res) => {
    const url = new URL(req.url, "http://localhost");
    let path = normalize(decodeURIComponent(url.pathname)).replace(/^(\.\.[/\\])+/, "");
    if (path.endsWith("/")) path += "index.html";
    const candidates = [path, `${path}.html`, join(path, "index.html")];
    for (const candidate of candidates) {
      const file = join(root, candidate);
      try {
        if (!(await stat(file)).isFile()) continue;
        res.writeHead(200, { "content-type": TYPES[extname(file)] ?? "application/octet-stream" });
        res.end(await readFile(file));
        return;
      } catch {
        /* 次の候補へ */
      }
    }
    res.writeHead(404, { "content-type": "text/plain" });
    res.end("not found");
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => resolve({ server, port: server.address().port }));
  });
}

const failures = [];
const checks = [];

function check(name, ok, detail = "") {
  checks.push(name);
  if (!ok) failures.push(detail ? `${name} ── ${detail}` : name);
}




/** 押す。**押せなかったことを、落ちた項目として残す**（例外で止めない）。
 *
 * 止めてしまうと、1件目で打ち切られて残りが分からない。直すたびに
 * 開き直すことになる。
 */
async function tap(page, sel, name) {
  try {
    await page.locator(sel).first().tap({ timeout: 5000 });
    check(name, true);
    return true;
  } catch (e) {
    const why = String(e).match(/intercepts pointer events|not visible|outside of the viewport/)?.[0];
    check(name, false, why ? `押せない（${why}）` : String(e).split("\n")[0]);
    return false;
  }
}

/** このサイトの落ち度だけを拾う。
 *
 * **外に出る取得の失敗は、この検査で判定できない。** 開発環境は TLS を
 * 挟む proxy を通しており Google Fonts が証明書で弾かれる。CI では通る。
 * **環境で結果が変わる検査は、あってもいつか黙って無視される。**
 *
 * 拾うのは
 *   - JS の例外（`pageerror`）── これは環境に依らずこちらの落ち度
 *   - サイト自身が配るはずのファイルの 404
 * favicon は Framework が置かないので数えない。
 */
function watch(page, sink) {
  page.on("pageerror", (e) => sink.push(`JS 例外: ${e}`));
  page.on("response", (r) => {
    const url = new URL(r.url());
    if (url.hostname !== "127.0.0.1") return;
    if (url.pathname === "/favicon.ico") return;
    if (r.status() >= 400) sink.push(`${r.status()} ${url.pathname}`);
  });
}

/** 電話の画面で一覧を開く。**利用者と同じ手順で押す。**
 *
 * Framework は `#observablehq-sidebar-toggle`（checkbox）自体を
 * 画面左端の縦帯として見せている（`position:fixed; width:2rem; height:100%`）。
 * **DOM を直に書き換えて開かない** ── それでは「指で開けるか」を
 * 確かめたことにならない。開く手段が消えたら、ここで落ちてほしい。
 */
async function openSidebar(page) {
  const toggle = page.locator("#observablehq-sidebar-toggle");
  if ((await toggle.count()) === 0) return false;
  if (await toggle.isChecked()) return true;
  try {
    await toggle.tap({ timeout: 4000 });
    const controls = page.locator(".chrome-controls").first();
    await controls.waitFor({ state: "visible", timeout: 4000 });
    // **滑り込みが終わるまで測らない。** 「見えた」は動き終わったことでは
    // ないので、途中の座標で当たり判定を見ると、site は正しいのに落ちる
    // （最初これで自分の検査が嘘の不具合を出した）
    let last = null;
    for (let i = 0; i < 40; i++) {
      const box = await controls.boundingBox();
      if (box && last && Math.abs(box.x - last.x) < 0.5 && box.x >= 0) return true;
      last = box;
      await page.waitForTimeout(50);
    }
    return false;
  } catch {
    return false;
  }
}

async function main() {
  const dirArg = process.argv.indexOf("--dir");
  const root = dirArg > -1 ? process.argv[dirArg + 1] : new URL("../dist/", import.meta.url).pathname;

  let pw;
  try {
    pw = await import("playwright");
  } catch {
    console.error("playwright が入っていません: npm install -D playwright");
    process.exit(2);
  }
  const { chromium, devices } = pw.default ?? pw;

  const { server, port } = await serve(root);
  const base = `http://127.0.0.1:${port}`;
  const browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH || undefined,
  });

  try {
    // **指で使う画面から見る。** 見逃した不具合はここにしか出なかった
    const phone = await browser.newContext({ ...devices["iPhone 13"] });
    const page = await phone.newPage();

    const consoleErrors = [];
    watch(page, consoleErrors);

    // サンプルページを見る。**数字が入った状態の画面**で、gold が空でも中身がある
    await page.goto(`${base}/sample`, { waitUntil: "networkidle" });
    await page.waitForTimeout(600);

    const controls = page.locator(".chrome-controls");
    check("左下の切り替えがある", (await controls.count()) === 1);

    // **電話では一覧が畳まれている。** 畳まれたまま押そうとすると
    // 「要素はあるのに見えない」になる ── 開いてから確かめる。
    // 開ける手段そのものが壊れていたら、そこで落とす
    const opened = await openSidebar(page);
    check("一覧を開ける", opened, "一覧を開く手段が見つからない、または開かない");
    if (!opened) throw new Error("一覧が開かないので、以降の確認ができない");

    // -- 押せること -------------------------------------------------------
    // **存在ではなく、その座標で本当に受け取るか。** 下敷きになっていると
    // 要素はあるのにタップが別の要素に吸われる（見逃した不具合はこれ）
    for (const [label, sel] of [
      ["ライト", '.chrome-switch button[data-value="light"]'],
      ["ENG", '.chrome-switch a[data-value="en"]'],
    ]) {
      const el = page.locator(sel).first();
      if ((await el.count()) === 0) {
        check(`${label} が押せる`, false, "要素が無い");
        continue;
      }
      const box = await el.boundingBox();
      if (!box) {
        check(`${label} が押せる`, false, "画面に出ていない");
        continue;
      }
      const x = box.x + box.width / 2;
      const y = box.y + box.height / 2;
      const top = await page.evaluate(
        ([cx, cy, s]) => {
          const hit = document.elementFromPoint(cx, cy);
          const want = document.querySelector(s);
          return { same: !!hit && !!want && (hit === want || want.contains(hit)), tag: hit?.tagName };
        },
        [x, y, sel]
      );
      check(
        `${label} が押せる`,
        top.same,
        top.same ? "" : `その座標を ${top.tag ?? "何か"} が受け取っている（下敷き）`
      );
      // 指の当たる大きさ。小さすぎると押せたり押せなかったりする
      check(`${label} の当たりが十分`, box.width >= 28 && box.height >= 24,
        `${Math.round(box.width)}x${Math.round(box.height)}`);
    }

    // -- 押した結果 -------------------------------------------------------
    await tap(page, '.chrome-switch button[data-value="light"]', "ライトを実際に押せた");
    await page.waitForTimeout(200);
    check(
      "ライトを押すと配色が変わる",
      (await page.evaluate(() => document.documentElement.getAttribute("data-theme"))) === "light"
    );

    await tap(page, '.chrome-switch button[data-value="dark"]', "ダークを実際に押せた");
    await page.waitForTimeout(200);
    check(
      "ダークに戻せる",
      (await page.evaluate(() => document.documentElement.getAttribute("data-theme"))) === "dark"
    );

    // -- 横にはみ出していないこと ----------------------------------------
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth
    );
    check("横にはみ出していない", overflow <= 1, `${overflow}px はみ出している`);

    // -- 言語の切り替えが 404 に飛ばないこと -----------------------------
    await tap(page, '.chrome-switch a[data-value="en"]', "ENG を実際に押せた");
    await page.waitForURL(/\/en\//, { timeout: 5000 }).catch(() => {});
    check("ENG が英語版に遷移する", /\/en\//.test(page.url()), `いまの URL: ${page.url()}`);
    check(
      "遷移先が 404 でない",
      !(await page.locator("body").innerText()).includes("not found")
    );
    const enOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth
    );
    check("英語版も横にはみ出していない", enOverflow <= 1, `${enOverflow}px`);

    await phone.close();

    // -- 広い画面 ---------------------------------------------------------
    const desktop = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const wide = await desktop.newPage();
    watch(wide, consoleErrors);
    await wide.goto(`${base}/sample`, { waitUntil: "networkidle" });
    await wide.waitForTimeout(600);

    // Alt-K で検索欄に合う（Framework の作法。**塞いでいないこと**の確認）
    await wide.keyboard.press("Alt+k");
    await wide.waitForTimeout(200);
    const focused = await wide.evaluate(() => {
      const el = document.activeElement;
      return { tag: el?.tagName, type: el?.getAttribute?.("type") };
    });
    check("Alt-K で検索欄に合う", focused.tag === "INPUT", `いまの焦点: ${focused.tag}`);

    await desktop.close();

    // -- コンソール -------------------------------------------------------
    // 出ているエラーは「気付いていないだけ」であることが多い
    check("コンソールにエラーが出ていない", consoleErrors.length === 0,
      consoleErrors.slice(0, 3).join(" / "));
  } finally {
    await browser.close();
    server.close();
  }

  console.log(`${checks.length} 項目を確認`);
  if (failures.length) {
    console.error("\n落ちた項目:");
    for (const f of failures) console.error(`  - ${f}`);
    process.exit(1);
  }
  console.log("すべて通過");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
