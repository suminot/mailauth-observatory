// 計器盤のリング表示。
//
// **出してよいのは「この計測がどこまで届いたか」だけ。** 達成度や準拠率を
// リングで大きく出すと、総合スコアの表示になる（DESIGN.md P8 が禁じている
// 「総合順位・A〜F グレード」と実質同じものになる）。
//
// 色も評価に使わない。リングは読み取り色 1色で描き、合格・要改善・未対応の
// 3段階とは別系統に置く。**リングが緑や赤に変わると、見た人はそれを成績
// として読む。**
//
// 動かさない。参考にした計器盤の意匠はリングが回転するが、ここは計測結果を
// 読む場所なので、注意を引く動きは足さない（styles.css 冒頭）。
//
// 色とフォントは CSS 側（.coverage svg）で当てる。SVG の表示属性に
// `var(--…)` を書いても解決しない環境があるため。

const SIZE = 132;
const STROKE = 9;
const R = (SIZE - STROKE) / 2 - 8;
const C = 2 * Math.PI * R;

/**
 * 観測できたドメインの割合をリングで示す。
 *
 * @param observed 観測できたドメイン数
 * @param total    計測対象にしたドメイン数
 */
export function coverageRing(observed, total, lang = "ja") {
  const wrap = document.createElement("div");

  const t = lang === "en" ? EN : JA;

  // **取れていないことを「0%」と描かない。** 未計測と 0 は別（原則5）
  if (!total) {
    wrap.className = "coverage-empty";
    wrap.textContent = t.empty;
    return wrap;
  }

  const got = observed ?? 0;
  const ratio = Math.max(0, Math.min(1, got / total));
  const missing = total - got;
  const c = SIZE / 2;

  wrap.className = "coverage";
  wrap.innerHTML = `
    <svg viewBox="0 0 ${SIZE} ${SIZE}" width="${SIZE}" height="${SIZE}"
         role="img" aria-label="計測対象 ${total} ドメインのうち ${got} から応答を得た">
      <circle class="track" cx="${c}" cy="${c}" r="${R}" fill="none" stroke-width="${STROKE}"/>
      <circle class="arc" cx="${c}" cy="${c}" r="${R}" fill="none" stroke-width="${STROKE}"
              stroke-dasharray="${(C * ratio).toFixed(2)} ${C.toFixed(2)}"
              transform="rotate(-90 ${c} ${c})"/>
      <text class="big" x="${c}" y="${c - 1}" text-anchor="middle">${(ratio * 100).toFixed(1)}%</text>
      <text class="cap" x="${c}" y="${c + 17}" text-anchor="middle">OBSERVED</text>
    </svg>
    <div class="coverage-note">${t.note(fmt(got), fmt(total), fmt(missing))}</div>`;
  return wrap;
}

const JA = {
  empty: "この母集団と月の観測はまだありません。",
  note: (got, total, missing) =>
    `<p><strong>${got}</strong> / ${total} ドメインから応答を得ました。</p>
     <p>残り <strong>${missing}</strong> は SERVFAIL・タイムアウト等で
        <strong>何も取れなかったドメイン</strong>です。未対応という意味ではありません。</p>
     <p>このページの率は、すべて ${got} を分母にしています。</p>`,
};

const EN = {
  empty: "There is no observation for this population and month yet.",
  note: (got, total, missing) =>
    `<p>A response was obtained from <strong>${got}</strong> of ${total} domains.</p>
     <p>The remaining <strong>${missing}</strong> returned SERVFAIL, timed out or
        otherwise <strong>gave nothing back</strong>. That does not mean they are
        unconfigured.</p>
     <p>Every share on this page uses ${got} as its denominator.</p>`,
};

function fmt(n) {
  return n === null || n === undefined ? "—" : n.toLocaleString("ja-JP");
}
