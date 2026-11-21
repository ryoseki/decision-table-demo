// ts-child-process-shell の検査用の架空コード（別名を付けた import）。実行しない（Semgrepが読むだけ）。
// 現在のSemgrep（固定した版）は、指定子が "child_process" のとき、別名を解決して検出する。
// "node:child_process" の別名は解決しない（docs/semgrep.md の「検査の範囲と限界」を参照）。
// import の範囲が、ほかの例に影響しないよう、このファイルには、検出する例だけを書く。
import * as cp from "child_process";
import { exec as run, execSync as runSync } from "child_process";

export function violations(name: string) {
  // ruleid: ts-child-process-shell
  cp.exec(`ls ${name}`);
  // ruleid: ts-child-process-shell
  run("ls " + name);
  // ruleid: ts-child-process-shell
  runSync(name);
}
