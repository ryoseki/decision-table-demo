// ts-eval-dynamic-code の検査用の架空コード。実行しない（Semgrepが読むだけ）。
// TypeScriptの eval は、固定の文字列でも検出する（Pythonの py-eval-exec-dynamic は、固定の文字列を除外する）。
export function violations() {
  // ruleid: ts-eval-dynamic-code
  return eval("1 + 1");
}
