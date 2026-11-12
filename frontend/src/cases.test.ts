// 実行: npm test（Vitest）。回答の集計と、表示用の状態を確認する。
import { expect, test } from "vitest";
import { cases, statusOf, tally } from "./data/cases";

const counts = (over: Partial<ReturnType<typeof tally>> = {}) => ({ yes: 0, no: 0, pending: 0, unanswered: 12, ...over });

test("状態: 結果があれば登録済み（確認待ちがあっても）", () => {
  expect(statusOf({ result: "注意", counts: counts({ pending: 1 }) })).toBe("登録済み");
});

test("状態: 結果がなく、確認待ちがあれば取得エラー。なければ未審査", () => {
  expect(statusOf({ counts: counts({ pending: 1, unanswered: 11 }) })).toBe("取得エラー");
  expect(statusOf({ counts: counts({ yes: 2, no: 1, unanswered: 9 }) })).toBe("未審査");
});

test("集計: Yes・No・確認待ち・未回答を数え、合計は設問数（12）になる", () => {
  const d = cases.find((c) => c.caseId === "CASE-D")!;
  expect(tally(d)).toEqual({ yes: 2, no: 2, pending: 1, unanswered: 7 });
  for (const c of cases) {
    const t = tally(c);
    expect(t.yes + t.no + t.pending + t.unanswered).toBe(12);
  }
});
