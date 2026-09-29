// 公開サイト全体に Basic 認証をかける（Cloudflare Pages Functions）。
//
// ## 値をここに書かない
//
// **このリポジトリは公開されている。** 利用者名とパスワードをソースに
// 書くと、認証をかけた意味がそのまま無くなる。GitHub secrets から
// Pages の環境変数に流し込み、ここでは読むだけにする
// （DESIGN.md「認証情報をハードコードしない」）。
//
// ## 設定が無ければ閉じる
//
// 環境変数が無いときに素通しさせると、**設定し忘れた状態が「認証が
// かかっているつもりのまま公開されている」状態になる。** 気付く手段が
// 無いので、閉じる側に倒す。デプロイの後に 401 が返ることを確かめる段を
// 置いてあるので、閉じっぱなしにも気付ける。

/** 秒単位で漏れる差を作らない素朴な定数時間比較。 */
function equals(a, b) {
  const x = new TextEncoder().encode(a ?? "");
  const y = new TextEncoder().encode(b ?? "");
  // 長さが違うことは隠せない（Basic 認証では実害が小さい）。
  // 中身の比較で早期に抜けないことだけを守る
  if (x.length !== y.length) return false;
  let diff = 0;
  for (let i = 0; i < x.length; i++) diff |= x[i] ^ y[i];
  return diff === 0;
}

function unauthorized() {
  return new Response("401 Unauthorized", {
    status: 401,
    headers: {
      "WWW-Authenticate": 'Basic realm="restricted", charset="UTF-8"',
      "Content-Type": "text/plain; charset=utf-8",
      // 認証を求める応答を中間に持たせない
      "Cache-Control": "no-store",
    },
  });
}

export async function onRequest(context) {
  const { request, env, next } = context;
  const user = env.BASIC_AUTH_USER;
  const pass = env.BASIC_AUTH_PASS;

  // **設定が無ければ閉じる。** 素通しにしない
  if (!user || !pass) return unauthorized();

  const header = request.headers.get("Authorization") ?? "";
  const [scheme, encoded] = header.split(" ");
  if (scheme !== "Basic" || !encoded) return unauthorized();

  let decoded;
  try {
    decoded = atob(encoded);
  } catch {
    return unauthorized();
  }
  // 利用者名に `:` は入らない。**最初の `:` で分ける**
  const sep = decoded.indexOf(":");
  if (sep < 0) return unauthorized();
  const givenUser = decoded.slice(0, sep);
  const givenPass = decoded.slice(sep + 1);

  // **両方を必ず比べる。** 片方が違った時点で戻ると、比較の回数から
  // どちらが違ったかが分かる
  const okUser = equals(givenUser, user);
  const okPass = equals(givenPass, pass);
  if (!(okUser && okPass)) return unauthorized();

  const response = await next();
  // 認証の内側なので、共有のキャッシュには置かせない
  const out = new Response(response.body, response);
  out.headers.set("Cache-Control", "private, no-store");
  return out;
}
