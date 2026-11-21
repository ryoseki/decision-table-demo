// ts-child-process-shell の検査用の架空コード（自前の exec 関数）。実行しない（Semgrepが読むだけ）。
// child_process から別の関数だけを import していても、同名の自前の関数と、オブジェクトの exec は、検出しない。
// このファイルに、child_process から exec・execSync を import する行を足さない。別のファイルに書く。
import { execFileSync } from "node:child_process";

function exec(command: string) {
  return command;
}

const tool = {
  exec(command: string) {
    return command;
  },
};

export function controls(name: string) {
  // ok: ts-child-process-shell
  exec(`ls ${name}`);
  // ok: ts-child-process-shell
  tool.exec(`ls ${name}`);
  // ok: ts-child-process-shell
  return execFileSync("echo", [name]);
}
