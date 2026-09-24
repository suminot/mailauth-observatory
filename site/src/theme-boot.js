// 配色を**描画前に**決める。
//
// これだけ同期で読み込んでいるのは、後から当てると暗い配色を選んでいる人に
// 一瞬白い画面が出るためである（描画が始まってから属性が付くので、
// その間は既定の配色で描かれる）。**chrome.js に混ぜると遅い。**
//
// 保存していない場合は端末の設定に従う。ここでは属性を付けないでおき、
// CSS 側の `@media (prefers-color-scheme: light)` に任せる。属性を
// 書き込んでしまうと、端末の設定を後から変えても追従しなくなる。

(function () {
  try {
    var saved = localStorage.getItem("mailauth.theme");
    if (saved === "light" || saved === "dark") {
      document.documentElement.setAttribute("data-theme", saved);
    }
  } catch (e) {
    // localStorage が使えない環境（プライベートウィンドウ等）。
    // **端末の設定で表示できるので、ここで止めない**
  }
})();
