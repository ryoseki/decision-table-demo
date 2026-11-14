// APIの呼び出し（画面は相対パス /api/... を呼ぶ。開発とE2EではViteが中継し、本番は同じオリジン）。
// Reactに依存しない。通信の失敗は ApiError にそろえる（status 0 は、通信できなかった場合）。
import { RESULT_CODE_BY_NAME, resultNameOf } from "./resultCodes";
import type { Answer, ResultName, ReviewCase, Tally } from "./data/cases";
import type { QuestionId } from "./data/questions";
import { QUESTION_IDS, type Rule } from "./matching";

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** 一覧の1行（GET /api/cases）。 */
export type CaseListRow = {
  caseId: string;
  userId: string;
  summary: string;
  inquiry: string;
  result?: ResultName;
  counts: Tally;
};

async function request(path: string, init?: RequestInit): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new ApiError(0, "サーバーに接続できませんでした");
  }
  if (!response.ok) throw new ApiError(response.status, `サーバーがエラーを返しました（HTTP ${response.status}）`);
  return response;
}

type ApiListRow = {
  case_id: string;
  summary: string;
  user_id: string;
  inquiry: { subject: string; body: string };
  result_code: string | null;
  counts: Tally;
};

/** 案件の一覧。並びは、APIが返す順（案件ID順）のまま。 */
export async function listCases(): Promise<CaseListRow[]> {
  const rows = (await (await request("/api/cases")).json()) as ApiListRow[];
  return rows.map((r) => ({
    caseId: r.case_id,
    userId: r.user_id,
    summary: r.summary,
    inquiry: r.inquiry.body,
    ...(r.result_code === null ? {} : { result: resultNameOf(r.result_code) }),
    counts: r.counts,
  }));
}

type ApiCase = {
  case_id: string;
  summary: string;
  user: { user_id: string };
  inquiry: { subject: string; body: string };
  questions: { question_code: string; answer: Answer | null }[];
  result: { result_code: string } | null;
};

/** 案件の詳細。未回答（回答の行がない）は、answers に入れない。 */
export async function getCase(caseId: string): Promise<ReviewCase> {
  const c = (await (await request(`/api/cases/${encodeURIComponent(caseId)}`)).json()) as ApiCase;
  const answers: ReviewCase["answers"] = {};
  for (const q of c.questions) {
    if (q.answer !== null) answers[q.question_code as QuestionId] = q.answer;
  }
  return {
    caseId: c.case_id,
    userId: c.user.user_id,
    summary: c.summary,
    inquiry: c.inquiry.body,
    answers,
    ...(c.result === null ? {} : { result: resultNameOf(c.result.result_code) }),
  };
}

type ApiRule = {
  rule_id: string;
  revision: number;
  result_code: string;
  conditions: Record<string, string>;
};

/** APIのルールを、照合用の形にする。この画面の照合は、A01〜A12すべての条件を持つルールだけを扱う。 */
function toRule(r: ApiRule): Rule {
  const expected = {} as Record<QuestionId, "yes" | "no">;
  for (const q of QUESTION_IDS) {
    const value = r.conditions[q];
    if (value !== "yes" && value !== "no") {
      throw new Error(`ルール${r.rule_id}の条件に、${q}のYes／Noがありません（この画面は12問すべての条件を持つルールだけを扱います）`);
    }
    expected[q] = value;
  }
  return { id: r.rule_id, result: resultNameOf(r.result_code), expected };
}

/** 照合に使うルール。APIが返す順（rule_id順）のまま。 */
export async function listRules(): Promise<Rule[]> {
  const rows = (await (await request("/api/rules")).json()) as ApiRule[];
  return rows.map(toRule);
}

/** 人が選んだ回答（Yes・No）。確認待ち・未回答に戻す操作はない。 */
export type HumanAnswers = Partial<Record<QuestionId, "yes" | "no">>;

/** ［判定する］。結果と、**変更した回答だけ**を保存する（変更がなければ answers は送らない）。 */
export async function saveJudgment(caseId: string, result: ResultName, answers: HumanAnswers = {}): Promise<void> {
  const body: { result_code: string; answers?: HumanAnswers } = { result_code: RESULT_CODE_BY_NAME[result] };
  if (Object.keys(answers).length > 0) body.answers = answers;
  await request(`/api/cases/${encodeURIComponent(caseId)}/judgment`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** 画面に出す、失敗の説明。 */
export function describeError(error: unknown): string {
  return error instanceof ApiError ? error.message : "予期しないエラーが発生しました";
}
