// 配色を**描画前に**決める。
//
// これだけ同期で読み込んでいるのは、後から当てると暗い配色を選んでいる人に
// 一瞬白い画面が出るためである（描画が始まってから属性が付くので、
// その間は既定の配色で描かれる）。**chrome.js に混ぜると遅い。**
//
// **既定はダーク。** この画面は暗い地が本来の姿なので、端末の設定が
// 明るくても暗いまま出す。明るくしたい人は左下で選べて、その選択は覚える。
//
// 端末の設定に従う作りにしていたが、運営者の指示で既定を固定した。
// 引き換えに、端末の設定を変えても追従しなくなる。

(function () {
  // **言語も描画前に決める。** 一覧は日英の両方を出力しているので、
  // 絞り込みが後になると、開いた瞬間だけ12項目が並んで見える。
  // CSS 側でどちらを隠すかを、この属性で決める
  var p = location.pathname;
  document.documentElement.setAttribute(
    "data-lang", p === "/en" || p === "/en/" || p.indexOf("/en/") === 0 ? "en" : "ja"
  );
  // JS が動いているときだけ一覧を隠す。**動かない環境では隠さない**
  // （隠したままになると、行き先が1つも見えなくなる）
  document.documentElement.setAttribute("data-js", "");

  try {
    var saved = localStorage.getItem("mailauth.theme");
    document.documentElement.setAttribute(
      "data-theme", saved === "light" ? "light" : "dark"
    );
  } catch (e) {
    // 読めなくても既定のダークにはする
    document.documentElement.setAttribute("data-theme", "dark");
    // localStorage が使えない環境（プライベートウィンドウ等）。
    // **端末の設定で表示できるので、ここで止めない**
  }
})();
