// ts-eval-dynamic-code の検査用の架空コード。実行しない（Semgrepが読むだけ）。
import vm from "node:vm";

export function violations(code: string) {
  // ruleid: ts-eval-dynamic-code
  eval(code);
  // ruleid: ts-eval-dynamic-code
  const f = new Function("a", code);
  // ruleid: ts-eval-dynamic-code
  setTimeout("tick()", 10);
  // ruleid: ts-eval-dynamic-code
  setInterval(`${code}()`, 10);
  // ruleid: ts-eval-dynamic-code
  vm.runInNewContext(code, {});
  // ruleid: ts-eval-dynamic-code
  const g = Function("a", code);
  // ruleid: ts-eval-dynamic-code
  vm.runInThisContext(code);
  // ruleid: ts-eval-dynamic-code
  vm.runInContext(code, {});
  // ruleid: ts-eval-dynamic-code
  setTimeout(`${code}()`, 10);
  return [f, g];
}

export function controls(callback: () => void) {
  // ok: ts-eval-dynamic-code
  setTimeout(callback, 10);
  // ok: ts-eval-dynamic-code
  const parsed = JSON.parse("{}");
  return parsed;
}
