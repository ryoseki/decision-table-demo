// 回答とルールの照合（画面表示用の純粋関数）。backend/app/rule_matching.py と同じ規則。条件のない設問は「問わない」。
// ルールは API から取得して渡す（GET /api/rules）。業務上の認定ルールではなく、架空の組み合わせ。
// 照合結果は表示（通常／グレー）の判断材料であり、人が選べる判定結果を制限しない。
// HTTP・DB・保存は扱わない。
import type { Answer, ResultName } from "./data/cases";
import type { QuestionId } from "./data/questions";

export const QUESTION_IDS: readonly QuestionId[] = [
  "A01", "A02", "A03", "A04", "A05", "A06", "A07", "A08", "A09", "A10", "A11", "A12",
];

/** 定義済みの判定結果（重い順）。照合結果にかかわらず、すべて選べる。 */
export const RESULTS: readonly ResultName[] = [
  "アカウント削除", "アカウント一時凍結", "注意", "問題なし",
];

export type Expected = "yes" | "no";
export type Rule = {
  id: string;
  /** ルールの版（不変の行。条件を変えるときは新しい版が増える） */
  revision: number;
  result: ResultName;
  /** 条件のある設問だけ。ない設問は「問わない」（どの回答でも食い違いにしない）。 */
  expected: Readonly<Partial<Record<QuestionId, Expected>>>;
};

/** 回答。未回答は undefined。確認待ちは "pending"。 */
export type AnswerMap = Partial<Record<QuestionId, Answer>>;

export type ConditionState = "match" | "mismatch" | "unanswered" | "pending" | "any";

export function conditionState(answer: Answer | undefined, expected: Expected): ConditionState {
  if (answer === undefined) return "unanswered";
  if (answer === "pending") return "pending";
  return answer === expected ? "match" : "mismatch";
}

export type RuleRow = {
  rule: Rule;
  /** 1問でもYes／Noが食い違えばグレー。未回答・確認待ちは食い違いにしない。 */
  gray: boolean;
  states: Readonly<Record<QuestionId, ConditionState>>;
};

export function matchRules(answers: AnswerMap, rules: readonly Rule[]): RuleRow[] {
  return rules.map((rule) => {
    const states = Object.fromEntries(
      QUESTION_IDS.map((q) => {
        const expected = rule.expected[q];
        return [q, expected === undefined ? "any" : conditionState(answers[q], expected)];
      }),
    ) as Record<QuestionId, ConditionState>;
    return { rule, gray: QUESTION_IDS.some((q) => states[q] === "mismatch"), states };
  });
}

/**
 * 未回答の設問が、照合を絞るのに不要か（画面案の左側グレー化の規則）。
 * 食い違わない行のうち、他の設問の条件が同じ組ごとに、当該設問がYesの行とNoの行の結果集合が一致するときだけ不要。
 * 食い違わない行が0件なら不要としない。
 */
export function canSkipQuestion(
  question: QuestionId,
  answers: AnswerMap,
  rules: readonly Rule[],
): boolean {
  const live = matchRules(answers, rules).filter((r) => !r.gray).map((r) => r.rule);
  if (live.length === 0) return false;
  const branches = new Map<string, { yes: Set<string>; no: Set<string> }>();
  for (const rule of live) {
    // 他の設問の条件が同じ組にまとめる。条件のない設問は "-"（問わない）として、別の組にする。
    const key = QUESTION_IDS.filter((q) => q !== question).map((q) => rule.expected[q] ?? "-").join("");
    let b = branches.get(key);
    if (b === undefined) {
      b = { yes: new Set(), no: new Set() };
      branches.set(key, b);
    }
    // 当該設問の条件がないルールは、Yesでも Noでも当てはまるので、両方に数える。
    const expected = rule.expected[question];
    for (const side of expected === undefined ? (["yes", "no"] as const) : ([expected] as const)) {
      b[side].add(rule.result);
    }
  }
  return [...branches.values()].every(
    (b) => b.yes.size === b.no.size && [...b.yes].every((r) => b.no.has(r)),
  );
}
