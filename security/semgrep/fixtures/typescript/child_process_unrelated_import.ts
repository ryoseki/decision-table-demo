// ts-child-process-shell の検査用の架空コード（child_process ではないモジュールの exec）。
// 実行しない（Semgrepが読むだけ）。
// このファイルに child_process の import を足さない。import の範囲が、この例に影響するため、別のファイルに書く。
import { exec } from "./runner";

export function controls(name: string) {
  // ok: ts-child-process-shell
  exec(`ls ${name}`);
}
