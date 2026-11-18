// 実行可能なエッジのテスト（CloudFront Functionsは使わず、同じ関数をNodeで呼ぶ）。
//   bash scripts/edge-test.sh
// 架空のIP（ドキュメント用の範囲）だけを使う。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'viewer-ip.js'), 'utf8');
const sandbox = {};
vm.runInNewContext(source + '\nthis.handler = handler;', sandbox);
const handler = sandbox.handler;

function event(ip, headers) {
  return {
    version: '1.0',
    context: { eventType: 'viewer-request' },
    viewer: ip === undefined ? {} : { ip: ip },
    request: {
      method: 'POST',
      uri: '/api/admin/question-proposals',
      querystring: {},
      headers: headers || {},
      cookies: {},
    },
  };
}

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

test('IPv4: 専用ヘッダーが、ポートなしのIP1つになる', () => {
  const out = handler(event('192.0.2.10'));
  assert.deepEqual(plain(out.headers['x-demo-viewer-ip']), { value: '192.0.2.10' });
});

test('IPv4射影のIPv6は、そのまま1つの値になる', () => {
  const out = handler(event('::ffff:192.0.2.10'));
  assert.deepEqual(plain(out.headers['x-demo-viewer-ip']), { value: '::ffff:192.0.2.10' });
});

test('IPv6: そのまま1つの値になる', () => {
  const out = handler(event('2001:db8:1:2::1'));
  assert.deepEqual(plain(out.headers['x-demo-viewer-ip']), { value: '2001:db8:1:2::1' });
});

// バックエンドの viewer_key（backend/tests/test_public_write.py の TestViewerKey）と同じ値。
// IPv4射影の16進形・大文字・全桁・省略形が、エッジで400にならず、そのまま1つの値でオリジンへ渡ること。
// 渡した値の正規化（IPv4射影→IPv4、IPv6→/64）はアプリが行う。
test('アプリが受け付けるIPの形は、エッジで400にならず、そのまま1つの値になる', () => {
  const accepted = [
    '192.0.2.1',
    '::ffff:192.0.2.1',
    '::ffff:c000:201',
    '::FFFF:C000:201',
    '2001:db8:1:2::1',
    '2001:DB8:1:2:ffff:ffff:ffff:ffff',
    '2001:0db8:0001:0002:0000:0000:0000:0001',
    '2001:db8::',
    '::1',
  ];
  for (const ip of accepted) {
    const out = handler(event(ip));
    assert.equal(out.statusCode, undefined, ip);
    assert.deepEqual(plain(out.headers['x-demo-viewer-ip']), { value: ip }, ip);
  }
});

test('エッジは粗い形の確認だけ。IPの文法の検証は、アプリが行う（viewer.ip が実IPである前提）', () => {
  // 形だけ通る値。CloudFrontが実際にこの値を渡すことはない前提。通った場合はアプリが拒否する
  // （同じ値を backend の TestViewerKey が拒否することを確認している）
  for (const ip of ['999.1.1.1', ':::', '1:2:3:4:5:6:7:8:9', '12345::1', '::ffff:999.1.1.1']) {
    assert.equal(handler(event(ip)).statusCode, undefined, ip);
  }
});

test('偽の単一ヘッダーは、実際のIPで置き換わる', () => {
  const out = handler(event('192.0.2.10', { 'x-demo-viewer-ip': { value: '203.0.113.9' } }));
  assert.deepEqual(plain(out.headers['x-demo-viewer-ip']), { value: '192.0.2.10' });
});

test('偽の重複ヘッダー（multiValue）は、項目ごと捨てて、1つの値だけにする', () => {
  const forged = {
    value: '203.0.113.1',
    multiValue: [{ value: '203.0.113.1' }, { value: '203.0.113.2' }],
  };
  const out = handler(event('192.0.2.10', { 'x-demo-viewer-ip': forged }));
  assert.deepEqual(plain(out.headers['x-demo-viewer-ip']), { value: '192.0.2.10' });
  assert.equal('multiValue' in out.headers['x-demo-viewer-ip'], false);
});

test('大文字小文字の違う偽ヘッダーも残らない', () => {
  const out = handler(
    event('192.0.2.10', {
      'X-Demo-Viewer-IP': { value: '203.0.113.1' },
      'x-internal-mock-token': { value: 'forged' },
      'X-Internal-Mock-Token': { value: 'forged2' },
    })
  );
  assert.deepEqual(Object.keys(out.headers), ['x-demo-viewer-ip']);
  assert.deepEqual(plain(out.headers['x-demo-viewer-ip']), { value: '192.0.2.10' });
});

test('内部モックのトークンは、単一でも複数値でも捨てる', () => {
  const out = handler(
    event('192.0.2.10', {
      'x-internal-mock-token': { value: 'a', multiValue: [{ value: 'a' }, { value: 'b' }] },
    })
  );
  assert.equal('x-internal-mock-token' in out.headers, false);
});

test('本文ハッシュ・依頼ID・Content-Type・他のヘッダーと要求の他の項目は変えない', () => {
  const headers = {
    'content-type': { value: 'application/json' },
    'idempotency-key': { value: '11111111-2222-3333-4444-555555555555' },
    'x-amz-content-sha256': { value: 'ab'.repeat(32) },
    accept: { value: 'application/json', multiValue: [{ value: 'a' }, { value: 'b' }] },
  };
  const out = handler(event('192.0.2.10', plain(headers)));
  for (const name of Object.keys(headers)) {
    assert.deepEqual(plain(out.headers[name]), headers[name], name);
  }
  assert.equal(out.method, 'POST');
  assert.equal(out.uri, '/api/admin/question-proposals');
  assert.deepEqual(plain(out.querystring), {});
  assert.deepEqual(plain(out.cookies), {});
});

test('旧ヘッダー（cloudfront-viewer-address）の値は、作らず・使わない', () => {
  const out = handler(
    event('192.0.2.10', { 'cloudfront-viewer-address': { value: '203.0.113.1:1' } })
  );
  assert.deepEqual(plain(out.headers['x-demo-viewer-ip']), { value: '192.0.2.10' });
});

test('汚れた・欠けた接続元IPは、要求を返さず400で止める（閉じて失敗）', () => {
  const dirty = [
    undefined,
    null,
    '',
    ' ',
    '1',
    '192.0.2.1:80',
    '[2001:db8::1]',
    '192.0.2.1, 203.0.113.1',
    ' 192.0.2.1',
    '192.0.2.1\n',
    'example.com',
    '2001:db8::1%eth0',
    'a'.repeat(46),
    { value: '192.0.2.1' },
    ['192.0.2.1'],
    42,
  ];
  for (const ip of dirty) {
    const out = handler(event(ip, { 'x-demo-viewer-ip': { value: '203.0.113.9' } }));
    assert.equal(out.statusCode, 400, JSON.stringify(ip));
    assert.equal(out.headers, undefined, JSON.stringify(ip));
  }
});

test('viewer自体が無いときも400で止める', () => {
  const input = event('192.0.2.1');
  delete input.viewer;
  assert.equal(handler(input).statusCode, 400);
});

test('ES5を超える構文を使っていない（CloudFront Functionsの互換のため）', () => {
  assert.doesNotMatch(source, /=>|\blet\b|\bconst\b|`|\bimport\b|\brequire\b/);
});
