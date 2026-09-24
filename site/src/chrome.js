// 画面の枠まわり。左の一覧に節を畳んで出し、下端に切り替えを置く。
//
// ## 右の目次をやめた理由
//
// Observable Framework は右端に目次を出すが、左に一覧、右に目次があると、
// **どちらを見ればよいかを読むたびに考えることになる。** 行き先は一箇所に
// まとめる。開いているページの節は、その一覧の中に一段下げて出す。
//
// ## 動かさない
//
// styles.css の方針どおり、開閉に動きを付けない。**いま開いているページの
// 節だけを出す**ので、畳んだり伸ばしたりする操作そのものが要らない。
// 節を読み込むのは静的な HTML の見出しからで、ページ遷移のたびに作り直す。

const THEME_KEY = "mailauth.theme";
const LANG_KEY = "mailauth.lang";

/** 節へのリンクを、いま開いているページの下に一段下げて入れる。 */
function buildSectionLinks() {
  const sidebar = document.querySelector("#observablehq-sidebar");
  const main = document.querySelector("#observablehq-main");
  if (!sidebar || !main) return;

  const active = sidebar.querySelector("li.observablehq-link-active");
  if (!active) return;
  active.querySelector(".section-links")?.remove();

  // 節の見出しだけを拾う。h1 は題字なので入れない
  const heads = [...main.querySelectorAll("h2[id]")];
  if (!heads.length) return;

  const list = document.createElement("ol");
  list.className = "section-links";
  for (const h of heads) {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = `#${h.id}`;
    a.textContent = h.textContent.trim();
    li.appendChild(a);
    list.appendChild(li);
  }
  active.appendChild(list);
  return { list, heads };
}

/** いま画面に出ている節に印を付ける。読んでいる位置が分かるようにする。 */
function trackCurrentSection(built) {
  if (!built || !("IntersectionObserver" in window)) return;
  const { list, heads } = built;
  const links = new Map(
    [...list.querySelectorAll("a")].map((a) => [a.getAttribute("href").slice(1), a])
  );
  const seen = new Set();

  const mark = () => {
    // **一番上に出ているものを選ぶ。** 複数が同時に見えるので、
    // 「最後に入ったもの」にすると下へ飛ぶ
    let top = null;
    for (const h of heads) if (seen.has(h.id)) { top = h.id; break; }
    for (const [id, a] of links) {
      if (id === top) a.setAttribute("aria-current", "true");
      else a.removeAttribute("aria-current");
    }
  };

  const io = new IntersectionObserver(
    (entries) => {
      for (const e of entries) {
        if (e.isIntersecting) seen.add(e.target.id);
        else seen.delete(e.target.id);
      }
      mark();
    },
    // 上端から少し下げた帯に入ったものを「読んでいる節」とみなす
    { rootMargin: "-80px 0px -70% 0px" }
  );
  for (const h of heads) io.observe(h);
}

/** 左下の切り替え。配色と言語を並べて置く。 */
function buildControls() {
  const sidebar = document.querySelector("#observablehq-sidebar");
  if (!sidebar || sidebar.querySelector(".chrome-controls")) return;

  const box = document.createElement("div");
  box.className = "chrome-controls";
  box.appendChild(themeToggle());
  box.appendChild(langToggle());
  sidebar.appendChild(box);
}

/** いまの配色。**既定はダーク**（theme-boot.js が描画前に付けている）。 */
function currentTheme() {
  return document.documentElement.getAttribute("data-theme") === "light"
    ? "light"
    : "dark";
}

function themeToggle() {
  const wrap = document.createElement("div");
  wrap.className = "chrome-switch";
  wrap.setAttribute("role", "group");
  wrap.setAttribute("aria-label", "配色");

  for (const [value, label] of [["dark", "ダーク"], ["light", "ライト"]]) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = label;
    b.dataset.value = value;
    b.addEventListener("click", () => {
      document.documentElement.setAttribute("data-theme", value);
      try {
        localStorage.setItem(THEME_KEY, value);
      } catch (e) {
        // 保存できなくても、この画面の見え方は変わる。**止めない**
      }
      sync(wrap, value);
    });
    wrap.appendChild(b);
  }
  sync(wrap, currentTheme());
  return wrap;
}

/** いまの言語。theme-boot.js が描画前に決めている。 */
function currentLang() {
  return document.documentElement.getAttribute("data-lang") === "en" ? "en" : "ja";
}

/** 同じページの、もう片方の言語の住所。
 *
 * **対応表は持たず、道の形で決める**（`/x` ↔ `/en/x`）。表を持つと、
 * ページを足したときに片方だけ増えて、切り替えが 404 に飛ぶ。
 * 両言語に同じページがあることは検査で縛っている。
 */
function counterpartPath() {
  const p = location.pathname;
  if (currentLang() === "en") {
    const rest = p.replace(/^\/en(\/|$)/, "/");
    return rest === "" ? "/" : rest;
  }
  return p === "/" ? "/en/" : "/en" + p;
}

/** 一覧を、いま開いている言語のものだけにする。
 *
 * Framework の一覧は1つしか持てないので、設定には日英の両方を並べ、
 * ここで片方を外す。**href は相対で出てくる**（`./en/data` や `../data`）
 * ので、属性の文字列ではなく**解決したあとの道で判定する。**
 */
function filterSidebarByLang() {
  const lang = currentLang();
  for (const a of document.querySelectorAll("#observablehq-sidebar li.observablehq-link > a")) {
    let path;
    try {
      path = new URL(a.href, location.href).pathname;
    } catch (e) {
      continue;
    }
    // 題字（一覧の先頭に出るサイト名）は消さずに行き先を差し替える。
    // **消すと英語版だけ題字が無くなる。** 設定に /en/ を別項目として
    // 足すと、今度は英語版だけ題字が本文の一覧に並んで体裁が変わる
    if (path === "/" || path === "/index.html") {
      if (lang === "en") a.href = a.href.replace(/\/(index\.html)?$/, "/en/$1");
      continue;
    }
    const isEn = path === "/en" || path === "/en/" || path.startsWith("/en/");
    if (isEn !== (lang === "en")) a.closest("li").remove();
  }
  // 絞り終わったことを CSS に伝える。ここで初めて一覧が見えるようになる
  document.documentElement.setAttribute("data-nav-ready", "");
}

/** 言語の切り替え。 */
function langToggle() {
  const wrap = document.createElement("div");
  wrap.className = "chrome-switch";
  wrap.setAttribute("role", "group");
  wrap.setAttribute("aria-label", "言語 / Language");

  const here = currentLang();
  const there = counterpartPath();
  for (const [value, label] of [["ja", "JPN"], ["en", "ENG"]]) {
    const a = document.createElement("a");
    a.textContent = label;
    a.dataset.value = value;
    a.setAttribute("lang", value);
    if (value === here) {
      a.href = location.pathname;
      a.setAttribute("aria-current", "true");
    } else {
      a.href = there;
      a.addEventListener("click", () => {
        try {
          localStorage.setItem(LANG_KEY, value);
        } catch (e) {
          /* 覚えられなくても遷移はする */
        }
      });
    }
    wrap.appendChild(a);
  }
  return wrap;
}

function sync(wrap, value) {
  for (const b of wrap.children) {
    if (b.dataset.value === value) b.setAttribute("aria-current", "true");
    else b.removeAttribute("aria-current");
  }
}

/** 検索の「Alt-K」を実際に効かせる。
 *
 * **Framework が出しているラベルと、実装が食い違っている。**
 * 検索欄の脇には Mac 以外で `Alt-K` と表示されるが、向こうの keydown は
 *
 *   e.code === "KeyK" && e.metaKey && !e.altKey && !e.ctrlKey
 *
 * という条件で、**Alt を明示的に除外している。** つまり拾われるのは
 * ⌘K（Mac）と `/` だけで、Alt-K はどこにも繋がっていない。
 * 押しても何も起きないショートカットを表示し続けるより、動くようにする。
 *
 * Mac では ⌘K が向こうで拾われているので、ここでは何もしない。
 */
function fixSearchShortcut() {
  const box = document.querySelector("#observablehq-search");
  const input = box?.querySelector("input");
  if (!input) return;
  // **表示も Alt-K に揃える。** Framework は navigator.platform が
  // Mac / iPhone なら ⌘K と出すが、iPhone で見ると ⌘K と表示された
  // うえに何も起きなかった（向こうのハンドラは metaKey しか見ない）。
  // 押せば動くものだけを表示する。⌘K は Mac で従来どおり効く
  box.setAttribute("data-shortcut", "Alt-K");

  addEventListener("keydown", (e) => {
    if (e.code !== "KeyK" || !e.altKey || e.metaKey || e.ctrlKey) return;
    e.preventDefault();
    // 狭い画面では一覧ごと隠れている。開いてから合わせる
    const toggle = document.querySelector("#observablehq-sidebar-toggle");
    if (toggle && !toggle.checked && input.offsetParent === null) toggle.checked = true;
    input.focus();
    input.select();
  });
}

function start() {
  filterSidebarByLang();
  trackCurrentSection(buildSectionLinks());
  buildControls();
  fixSearchShortcut();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", start, { once: true });
} else {
  start();
}
