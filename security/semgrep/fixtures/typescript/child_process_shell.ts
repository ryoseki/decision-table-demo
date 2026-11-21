// ts-child-process-shell の検査用の架空コード。実行しない（Semgrepが読むだけ）。
import * as childProcess from "child_process";
import { exec, execFileSync, execSync } from "node:child_process";

export function violations(name: string) {
  // ruleid: ts-child-process-shell
  exec(`ls ${name}`);
  // ruleid: ts-child-process-shell
  execSync("ls " + name);
  // ruleid: ts-child-process-shell
  childProcess.execSync(name);
  // ruleid: ts-child-process-shell
  childProcess.exec(`ls ${name}`);
}

export function controls(name: string) {
  // ok: ts-child-process-shell
  execFileSync("ls", [name]);
  // ok: ts-child-process-shell
  execSync("ls -l");
  // ok: ts-child-process-shell
  childProcess.exec("ls -l");
}
