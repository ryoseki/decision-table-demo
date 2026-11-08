import { defineConfig } from "@playwright/test";

// 配布先（またはローカルに展開したZIP）のスモーク確認用の設定。
// 既存のE2E設定（../playwright.config.ts）とは独立で、暗黙のlocalhostは使わない。
//   CD_BASE_URL     必須。オリジンのみ。例: https://example.invalid
//                   （認証情報・パス・クエリ・#を含むと拒否する）
//   CD_EXPECTED_SHA 必須。確認する40桁のコミットSHA（承認したSHAを渡す）
//   CD_ALLOW_LOCAL  "true" のときだけ、http・releaseEligible=false（ローカル統合確認）を許す。
//                   既定では許さない。配布の確認には使わない。
function required(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`環境変数 ${name} が必要です（暗黙の既定値は使いません）`);
  }
  return value;
}

export const baseUrl = new URL(required("CD_BASE_URL"));
export const expectedSha = required("CD_EXPECTED_SHA");
export const allowLocal = process.env.CD_ALLOW_LOCAL === "true";

if (!/^[0-9a-f]{40}$/.test(expectedSha)) {
  throw new Error("CD_EXPECTED_SHA は40桁の小文字16進数で指定してください");
}
if (baseUrl.protocol !== "https:" && !(allowLocal && baseUrl.protocol === "http:")) {
  throw new Error("CD_BASE_URL は https が必要です（http は CD_ALLOW_LOCAL=true のときだけ）");
}
// オリジンだけを受け付ける。認証情報・パス・クエリ・フラグメントを黙って捨てて
// 別のURLを確認してしまうことを防ぐ。
if (
  baseUrl.username ||
  baseUrl.password ||
  baseUrl.pathname !== "/" ||
  baseUrl.search ||
  baseUrl.hash
) {
  throw new Error(
    "CD_BASE_URL はオリジンのみ（例: https://host）。認証情報・パス・クエリ・#は不可です",
  );
}

// 結果の出力先は e2e/cd 配下に分離する（既存E2Eの設定が親ディレクトリを消すため）。
// CD_REPORT_ID（任意・安全な識別子）を指定すると、実行ごとのサブディレクトリに出力し、
// 過去の成功・失敗の記録を上書きしない。未指定なら _default サブディレクトリだけに出力する
// （再実行で _default のみ上書き。ルートは消さず、名前付きの記録は残る）。
// 安全な識別子は英数字で始まるため、_default と衝突しない。
const reportId = process.env.CD_REPORT_ID ?? "";
if (reportId !== "" && !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(reportId)) {
  throw new Error("CD_REPORT_ID は英数字で始まる64文字以内の英数字・_・.・- で指定してください");
}
const subDir = reportId || "_default";
const resultsDir = `./test-results/${subDir}`;
const reportDir = `./playwright-report/${subDir}`;

export default defineConfig({
  testDir: "./tests",
  forbidOnly: true,
  workers: 1,
  retries: 0,
  // 全体と各テストに上限を設ける。接続できない場合も無限に待たない。
  globalTimeout: 3 * 60 * 1000,
  timeout: 30 * 1000,
  expect: { timeout: 10 * 1000 },
  outputDir: resultsDir,
  reporter: [["list"], ["html", { open: "never", outputFolder: reportDir }]],
  use: {
    baseURL: baseUrl.origin,
    actionTimeout: 10 * 1000,
    navigationTimeout: 15 * 1000,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
