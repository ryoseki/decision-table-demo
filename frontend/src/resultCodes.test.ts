// 実行: npm test（Vitest）。結果の表示名と、APIの結果コードの対応を確認する。
import { expect, test } from "vitest";
import { RESULTS } from "./matching";
import { RESULT_CODE_BY_NAME, resultNameOf } from "./resultCodes";

test("画面の4つの結果が、4つのコードに、1対1で対応する", () => {
  expect(RESULT_CODE_BY_NAME).toEqual({
    アカウント削除: "account_deletion",
    アカウント一時凍結: "account_suspension",
    注意: "caution",
    問題なし: "no_issue",
  });
  expect(Object.keys(RESULT_CODE_BY_NAME).sort()).toEqual([...RESULTS].sort());
  expect(new Set(Object.values(RESULT_CODE_BY_NAME)).size).toBe(4);
});

test("コードから表示名へ戻せる", () => {
  for (const [name, code] of Object.entries(RESULT_CODE_BY_NAME)) {
    expect(resultNameOf(code)).toBe(name);
  }
});

test("定義にないコードは、例外にする（別の結果に見せない）", () => {
  expect(() => resultNameOf("unknown")).toThrow("定義にない結果コード");
  expect(() => resultNameOf("")).toThrow();
});
