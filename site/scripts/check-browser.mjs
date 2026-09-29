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
//   - 検索欄を出していない（索引に入らないものを探させない）
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
/** `site/static/_headers` の `/*` ブロックをそのまま読む。
 *
 * **CSP は文字列で書いただけでは確かめたことにならない。** 本番と同じ
 * ヘッダを付けて配り、実際のページが違反しないことを見る。
 */
async function siteHeaders() {
  const path = new URL("../static/_headers", import.meta.url).pathname;
  let text;
  try {
    text = await readFile(path, "utf8");
  } catch {
    return {};
  }
  const out = {};
  let inGlobal = false;
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    if (!line || line.trimStart().startsWith("#")) continue;
    if (!line.startsWith(" ")) {
      inGlobal = line.trim() === "/*";
      continue;
    }
    if (!inGlobal) continue;
    const at = line.indexOf(":");
    if (at > 0) out[line.slice(0, at).trim()] = line.slice(at + 1).trim();
  }
  return out;
}

function serve(root, headers = {}) {
  const server = createServer(async (req, res) => {
    const url = new URL(req.url, "http://localhost");
    let path = normalize(decodeURIComponent(url.pathname)).replace(/^(\.\.[/\\])+/, "");
    if (path.endsWith("/")) path += "index.html";
    const candidates = [path, `${path}.html`, join(path, "index.html")];
    for (const candidate of candidates) {
      const file = join(root, candidate);
      try {
        if (!(await stat(file)).isFile()) continue;
        res.writeHead(200, {
          ...headers,
          "content-type": TYPES[extname(file)] ?? "application/octet-stream",
        });
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
  // **CSP 違反はコンソールにしか出ない。** 環境によらずこちらの落ち度なので
  // 拾う（外部への取得の失敗一般は拾わない ── 開発環境の proxy で変わる）
  page.on("console", (m) => {
    const t = m.text();
    if (m.type() === "error" && /Content Security Policy|Refused to/i.test(t)) {
      sink.push(`CSP 違反: ${t.slice(0, 160)}`);
    }
  });
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

  const headers = await siteHeaders();
  const { server, port } = await serve(root, headers);
  check(
    "本番のヘッダを付けて確かめている",
    Boolean(headers["Content-Security-Policy"]),
    "_headers から CSP を読めていない"
  );
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

    // -- 検索を出さない -----------------------------------------------------
    // **索引に入るのはページの地の文だけ。** 人が探すもの（ベンダー名・
    // 企業名・数字）は実行時に JSON から描いているので一度も入らない。
    // 実測で `IIJ` も `Microsoft` も 0 件だった。日本語版に英語版のページが
    // 混ざって出てもいた。**何も返ってこない入口は置かない**（#72 と同じ）
    const searchBox = await wide.evaluate(
      () => document.querySelectorAll("#observablehq-search").length
    );
    check("検索欄を出していない", searchBox === 0, `${searchBox} 個ある`);

    // 表に押せるだけで何も起きないものを置かない。
    // **`Inputs.table` は既定で選択用のチェックボックスを出す。** 選択を
    // 受け取っていないので押しても何も起きず、読み手の手を止めるだけ
    const boxes = await wide.evaluate(
      () => document.querySelectorAll("table input[type=checkbox]").length
    );
    check("表に意味の無いチェックボックスが無い", boxes === 0, `${boxes} 個ある`);

    // **題字からしか入れない状態にしない。** 左上のサイト名を押すと
    // ダッシュボードに戻るが、押せることは見て分からない
    await wide.goto(`${base}/data`, { waitUntil: "networkidle" });
    await wide.waitForTimeout(400);
    const nav = await wide.evaluate(() => {
      const lists = document.querySelectorAll("#observablehq-sidebar > ol");
      const items = [...(lists[lists.length - 1]?.querySelectorAll("li.observablehq-link > a") ?? [])];
      return {
        labels: items.map((a) => a.textContent.trim()),
        homes: items.filter((a) => new URL(a.href, location.href).pathname === "/").length,
      };
    });
    check(
      "一覧にダッシュボードがある",
      nav.labels.includes("ダッシュボード"),
      `いまの一覧: ${nav.labels.join(" / ")}`
    );
    check(
      "一覧に他方の言語が混ざっていない",
      !nav.labels.includes("Dashboard"),
      `いまの一覧: ${nav.labels.join(" / ")}`
    );
    check("ダッシュボードが重複していない", nav.homes === 1, `${nav.homes} 個ある`);

    // **英語側も見る。** 題字を道（`/`）で見分ける実装だと、日本語側は
    // 正しく出るのに英語側にだけ「ダッシュボード」が残って二重になる。
    // 片方しか見ないと、その壊れ方を見逃す（実際に見逃した）
    await wide.goto(`${base}/en/data`, { waitUntil: "networkidle" });
    await wide.waitForTimeout(400);
    const enNav = await wide.evaluate(() => {
      const lists = document.querySelectorAll("#observablehq-sidebar > ol");
      const items = [...(lists[lists.length - 1]?.querySelectorAll("li.observablehq-link > a") ?? [])];
      return items.map((a) => a.textContent.trim());
    });
    check(
      "英語版の一覧に Dashboard がある",
      enNav.includes("Dashboard"),
      `いまの一覧: ${enNav.join(" / ")}`
    );
    check(
      "英語版の一覧に日本語が混ざっていない",
      !enNav.includes("ダッシュボード"),
      `いまの一覧: ${enNav.join(" / ")}`
    );

    // -- 何から組んだかの表示 ---------------------------------------------
    // **番号の無いコミットで commit hash に落ちない。** 月次計測の
    // コミットは PR を経ないので件名に `(#NN)` が無く、以前はそこで
    // 短い commit に落ちていた。画面に `Build e23fedc` と出て、
    // 読み手には何のことか分からなかった。
    //
    // ここは**ビルド結果を読む**。設定の書き方を見るだけでは、
    // 実際にどう出たかを確かめたことにならない
    const build = await wide.evaluate(() => ({
      meta: document
        .querySelector('meta[name="mailauth-build"]')
        ?.content?.trim() ?? null,
      line: document.querySelector(".chrome-build")?.textContent?.trim() ?? null,
    }));
    check("何から組んだかが出ている", Boolean(build.meta), "meta が無い");
    check(
      "PR 番号で出ている（commit hash に落ちていない）",
      /^#\d+$/.test(build.meta ?? ""),
      `いまの値: ${build.meta}`
    );
    check(
      "番号が3桁以上でゼロ詰めされている",
      /^#\d{3,}$/.test(build.meta ?? ""),
      `いまの値: ${build.meta}`
    );

    // -- メール基盤のページ -----------------------------------------------
    // **限界の断りが表より前に出ていること。** 名指しはしていないが、
    // 「検出できなかった＝使っていない」と読ませるのが一番まずい読まれ方で、
    // それを止めるのは表の手前に置いた断りだけである
    await wide.goto(`${base}/platforms`, { waitUntil: "networkidle" });
    await wide.waitForTimeout(400);
    const plat = await wide.evaluate(() => {
      const main = document.querySelector("main");
      const limits = main?.querySelector(".limits");
      // **表ではなく層の見出しと比べる。** データが空の月は表が1つも
      // 描かれず、表と比べる書き方だと**何も確かめずに通ってしまう**
      // （最初そう書いて、空の月で素通りした）。見出しは常にある
      const firstLayer = [...(main?.querySelectorAll("h2") ?? [])].find((h) =>
        h.textContent.includes("実基盤")
      );
      const order =
        limits && firstLayer
          ? limits.compareDocumentPosition(firstLayer) &
            Node.DOCUMENT_POSITION_FOLLOWING
          : 0;
      const lists = document.querySelectorAll("#observablehq-sidebar > ol");
      const items = [
        ...(lists[lists.length - 1]?.querySelectorAll("li.observablehq-link > a") ?? []),
      ].map((a) => a.textContent.trim());
      return {
        hasLimits: Boolean(limits),
        limitsBeforeLayers: Boolean(order),
        headings: [...(main?.querySelectorAll("h2") ?? [])].map((h) =>
          h.textContent.trim()
        ),
        nav: items,
      };
    });
    check("基盤のページに限界の断りがある", plat.hasLimits, "`.limits` が無い");
    check(
      "限界の断りが層の見出しより前にある",
      plat.limitsBeforeLayers,
      "数字の後ろに出ている"
    );
    // **同じベンダーが1つの層に2回出ない。** 母集団で絞り忘れると、
    // 国内と海外が同じ表に並んで同じ名前が2行に出る。
    // **母集団が1つのうちは当たらない検査である**（米国が入って初めて効く）
    const dupes = await wide.evaluate(() => {
      const out = [];
      for (const tbl of document.querySelectorAll("main table")) {
        const heads = [...tbl.querySelectorAll("thead th")].map((h) =>
          h.textContent.trim()
        );
        const col = heads.indexOf("ベンダー");
        if (col < 0) continue;
        const seen = new Set();
        for (const row of tbl.querySelectorAll("tbody tr")) {
          const v = row.children[col]?.textContent?.trim();
          if (!v) continue;
          if (seen.has(v)) out.push(v);
          seen.add(v);
        }
      }
      return out;
    });
    check(
      "同じベンダーが1つの層に2回出ていない",
      dupes.length === 0,
      `重複: ${dupes.join(" / ")}`
    );
    check(
      "母集団を選べる",
      await wide.evaluate(() =>
        [...document.querySelectorAll("main label")].some((l) =>
          l.textContent.includes("母集団")
        )
      ),
      "母集団の選択が無い"
    );
    check(
      "層が3つとも出ている",
      ["実基盤", "受信の前段", "送信の前段"].every((h) =>
        plat.headings.some((x) => x.includes(h))
      ),
      `いまの見出し: ${plat.headings.join(" / ")}`
    );
    check(
      "一覧にメール基盤がある",
      plat.nav.includes("メール基盤"),
      `いまの一覧: ${plat.nav.join(" / ")}`
    );

    // **英語側も見る。** 片方しか見ないと壊れ方を見逃す（実際に見逃した）
    await wide.goto(`${base}/en/platforms`, { waitUntil: "networkidle" });
    await wide.waitForTimeout(400);
    const enPlat = await wide.evaluate(() => {
      const main = document.querySelector("main");
      const lists = document.querySelectorAll("#observablehq-sidebar > ol");
      const items = [
        ...(lists[lists.length - 1]?.querySelectorAll("li.observablehq-link > a") ?? []),
      ].map((a) => a.textContent.trim());
      // **表の中身は別に見る。** ベンダー名は実在の社名なので、
      // 「大塚商会」が英語版に出るのは正しい。訳せないし、訳したら
      // 検索もできない。**こちらが付けたラベルだけが英語であるべき。**
      const prose = [...(main?.querySelectorAll("h1,h2,h3,p,li") ?? [])]
        .map((e) => e.textContent)
        .join(" ");
      // **`td:first-child` では取れない。** `Inputs.table` は先頭に空の
      // セルを出すので、見出しから列の位置を引く（最初これで空振りした）
      const vendorCells = [];
      for (const tbl of main?.querySelectorAll("table") ?? []) {
        const heads = [...tbl.querySelectorAll("thead th")].map((h) =>
          h.textContent.trim()
        );
        const col = heads.indexOf("Vendor");
        if (col < 0) continue;
        for (const row of tbl.querySelectorAll("tbody tr")) {
          const cell = row.children[col];
          if (cell) vendorCells.push(cell.textContent.trim());
        }
      }
      return {
        hasLimits: Boolean(main?.querySelector(".limits")),
        proseJp: /[ぁ-んァ-ヶ]/.test(prose),
        // 秘匿の束ねは実在の会社ではなく集計上の入れ物。ここは訳す
        suppressed: vendorCells.filter((v) => /秘匿|suppressed/i.test(v)),
        nav: items,
      };
    });
    check("英語版にも限界の断りがある", enPlat.hasLimits, "`.limits` が無い");
    check(
      "英語版の地の文に日本語が混ざっていない",
      !enPlat.proseJp,
      "見出しか本文にかなが出ている"
    );
    check(
      "英語版で秘匿の束ねが英語になっている",
      enPlat.suppressed.length > 0 &&
        enPlat.suppressed.every((v) => !/[ぁ-んァ-ヶ]/.test(v)),
      `いまの値: ${enPlat.suppressed.join(" / ") || "（秘匿の行が無い）"}`
    );
    check(
      "英語版の一覧に Mail platforms がある",
      enPlat.nav.includes("Mail platforms"),
      `いまの一覧: ${enPlat.nav.join(" / ")}`
    );

    // -- 数字の入らない欄を空欄で出さない ---------------------------------
    // **無い列は 0 ではない**（原則5）。`sp=` とサブドメインの欄は、
    // その列が入るより前に計測した月の gold には**存在しない。**
    // `current?.x` は `undefined` になり、表にすると空のセルが並ぶ ──
    // 読み手には「該当なし」に見える。
    //
    // 数字が入るときと入らないときの**両方の状態を見る。** 表紙は実データ
    // （いまは列が無い月）、サンプルは数字の入った状態である。
    // 片方だけ見ると、もう片方の壊れ方を見逃す
    const sectionState = async (pageObj, url, headings) => {
      await pageObj.goto(url, { waitUntil: "networkidle" });
      await pageObj.waitForTimeout(500);
      return pageObj.evaluate((wanted) => {
        const out = {};
        const main = document.querySelector("main");
        for (const want of wanted) {
          const head = [...(main?.querySelectorAll("h2,h3") ?? [])].find((h) =>
            h.textContent.includes(want)
          );
          if (!head) {
            out[want] = { found: false };
            continue;
          }
          // **次の見出しまでを1節とみなす。** 深さを問わず切る ──
          // `h2` の節に `h3` の節の表まで含めると、別の節の空欄を
          // この節のものとして数える（最初そう書いて空振りした）
          const cells = [];
          let note = false;
          for (let el = head.nextElementSibling; el; el = el.nextElementSibling) {
            if (/^H[1-6]$/.test(el.tagName)) break;
            for (const tbl of el.querySelectorAll?.("table") ?? []) {
              // **`Inputs.table` は行の先頭に空のセルを出す。**
              // 見出しのある列だけを数字の欄として見る
              const heads = [...tbl.querySelectorAll("thead th")].map((h) =>
                h.textContent.trim()
              );
              for (const row of tbl.querySelectorAll("tbody tr")) {
                heads.forEach((label, i) => {
                  if (!label) return;
                  cells.push(row.children[i]?.textContent?.trim() ?? "");
                });
              }
            }
            if (el.matches?.("p.muted") || el.querySelector?.("p.muted")) note = true;
          }
          out[want] = { found: true, note, cells };
        }
        return out;
      }, headings);
    };

    for (const [label, url, headings, mustHaveNumbers] of [
      ["表紙", `${base}/`, ["自分と、その配下", "実際に引いたサブドメイン"], false],
      ["サンプル", `${base}/sample`, ["自分と、その配下", "実際に引いたサブドメイン"], true],
      [
        "英語版の表紙",
        `${base}/en/`,
        ["The domain, and what sits under it", "Subdomains actually queried"],
        false,
      ],
    ]) {
      const state = await sectionState(wide, url, headings);
      for (const [name, got] of Object.entries(state)) {
        check(`${label}に「${name}」の節がある`, got.found, "見出しが無い");
        if (!got.found) continue;
        // 表が出ているなら、**どのセルも空でないこと。**
        // 表も断りも無い、あるいは空欄が並ぶ状態を落とす
        const hasTable = got.cells.length > 0;
        const blank = got.cells.filter((c) => c === "").length;
        check(
          `${label}の「${name}」が空欄を並べていない`,
          got.note ? !hasTable : hasTable && blank === 0,
          got.note
            ? "計測していない断りと表が同時に出ている"
            : `表が無い、または空のセルが ${blank} 個ある`
        );
        // **サンプルは「数字が入るとこう見える」を見せるページである。**
        // 断りが出ていたら、それはサンプルの役目を果たしていない
        if (mustHaveNumbers) {
          check(
            `サンプルの「${name}」に数字が入っている`,
            hasTable && !got.note,
            got.note ? "計測していない断りが出ている" : "表が無い"
          );
        }
      }
    }

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
