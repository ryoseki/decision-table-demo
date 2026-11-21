// ts-child-process-shell の検査用の架空コード（require）。実行しない（Semgrepが読むだけ）。
// import の範囲が、ほかの例に影響しないよう、このファイルには、検出する例だけを書く。
const cp = require("child_process");
const { execSync: runSync } = require("child_process");

function violations(name) {
  // ruleid: ts-child-process-shell
  cp.exec(`ls ${name}`);
  // ruleid: ts-child-process-shell
  runSync("ls " + name);
}

module.exports = { violations };
