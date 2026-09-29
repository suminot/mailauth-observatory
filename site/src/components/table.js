// 表の共通の入口。
//
// **`Inputs.table` は既定で選択用のチェックボックスを出す。** あれは
// `view()` で選択結果を受け取るための仕掛けで、こちらの表はどれも
// ただ見せているだけなので、**押しても何も起きない。**
//
// 何も起きない操作要素を置くと、読み手は「選んで何かするのだろう」と
// 考えて手を止める。公開しているのは数字であって道具ではない。
//
// 1か所ずつ `select: false` を書き足す手もあったが、**次に表を足す人が
// また付ける。** 既定を変えられる入口を1つにして、`Inputs.table` を
// 直接呼ばないことを検査で決めている。
import * as Inputs from "npm:@observablehq/inputs";

/**
 * 読ませるための表。`Inputs.table` と同じ引数を取る。
 *
 * **選択が要る表を作るときは `select: true` を明示する。** 既定を
 * 上書きできるようにしてあるのは、そのとき理由が呼び出し側に残るため。
 */
export function table(data, options = {}) {
  return Inputs.table(data, { select: false, ...options });
}
