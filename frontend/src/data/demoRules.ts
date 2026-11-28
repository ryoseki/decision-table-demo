// テスト用の架空ルール（docs/decision-support-demo-rules.json の R01〜R03 と同じ組み合わせ）。
// 画面の本体は、ルールを API から取得する。本番のコードからは取り込まない。
import { QUESTION_IDS, type Expected, type Rule } from "../matching";
import type { QuestionId } from "./questions";

function parse(pattern: string): Record<QuestionId, Expected> {
  const marks = pattern.split(" ");
  if (marks.length !== QUESTION_IDS.length || marks.some((m) => m !== "Y" && m !== "N")) {
    throw new Error("Specify Y/N for all 12 questions");
  }
  return Object.fromEntries(
    QUESTION_IDS.map((q, i) => [q, marks[i] === "Y" ? "yes" : "no"]),
  ) as Record<QuestionId, Expected>;
}

export const DEMO_RULES: readonly Rule[] = [
  { id: "R01", revision: 1, result: "Caution", expected: parse("Y Y Y Y Y N Y Y Y N Y Y") },
  { id: "R02", revision: 1, result: "No issue", expected: parse("Y N Y Y Y Y Y Y Y Y Y N") },
  { id: "R03", revision: 1, result: "Account suspension", expected: parse("N Y Y Y Y N Y Y Y N Y Y") },
];
