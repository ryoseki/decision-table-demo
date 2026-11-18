// CloudFront Functions（viewer-request）。実際の接続元IPを、専用ヘッダーでオリジンへ渡す。
// 方針は docs/development-workflow.md「公開時の書き込みと利用枠」と infra/cd/cloudfront-plan.md を参照。
// - 値は event.viewer.ip だけ（ポートなしのIP1つ）。利用者が送った同名のヘッダーは、複数値
//   （multiValue）も含め、項目ごと捨てて置き換える。
// - 内部のモック用トークンのヘッダーは、利用者が送っても捨てる（インスタンス内だけで使う秘密）。
// - 接続元IPを取得できない・IPの形でないときは、要求をオリジンへ送らず400で止める（閉じて失敗）。
// - 本文・本文ハッシュ（x-amz-content-sha256）・依頼ID（idempotency-key）などは変えない。
// 外部のライブラリ・ES5を超える構文は使わない。このファイルのSHA-256が、ZIPの中の写し・
// manifest・CloudFrontのLIVEのコードと一致することを、配布（scripts/deploy-lambda.py）が確認する。
function handler(event) {
  var request = event.request;
  var viewer = event.viewer;
  var ip = viewer ? viewer.ip : undefined;
  var headers = request.headers;
  var names = Object.keys(headers);
  for (var i = 0; i < names.length; i++) {
    var lowered = names[i].toLowerCase();
    if (lowered === 'x-demo-viewer-ip' || lowered === 'x-internal-mock-token') {
      delete headers[names[i]];
    }
  }
  // IPv4（ドット4つ）か、IPv6（コロン2つ以上。末尾だけIPv4の形でもよい）だけを通す。
  // ポート・角括弧・空白・カンマ・スコープIDは通さない。厳密な検証はアプリ側でも行う。
  var ipv4 = /^[0-9]{1,3}(\.[0-9]{1,3}){3}$/;
  var ipv6 = /^[0-9A-Fa-f:]+$/;
  var ipv6Tail = /^[0-9A-Fa-f:]+:[0-9]{1,3}(\.[0-9]{1,3}){3}$/;
  var plausible =
    typeof ip === 'string' &&
    ip.length <= 45 &&
    (ipv4.test(ip) ||
      (ip.split(':').length > 2 && (ipv6.test(ip) || ipv6Tail.test(ip))));
  if (!plausible) {
    return { statusCode: 400, statusDescription: 'Bad Request' };
  }
  headers['x-demo-viewer-ip'] = { value: ip };
  return request;
}
