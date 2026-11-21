// ts-child-process-shell の検査用の架空コード（exec・execSync 以外の関数）。実行しない（Semgrepが読むだけ）。
// 第1引数が固定の文字列でなくても、シェルを通さない関数は検出しない。
// このファイルに、child_process から exec・execSync を import する行を足さない。別のファイルに書く。
import { execFileSync, spawn } from "node:child_process";

export function controls(command: string, name: string) {
  // ok: ts-child-process-shell
  execFileSync(command, [name]);
  // ok: ts-child-process-shell
  spawn(command, [name]);
}
