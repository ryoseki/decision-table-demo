// APIの呼び出し（画面は相対パス /api/... を呼ぶ。開発とE2EではViteが中継し、本番は同じオリジン）。
// Reactに依存しない。通信の失敗は ApiError にそろえる（status 0 は、通信できなかった場合）。
import { RESULT_CODE_BY_NAME, resultNameOf } from "./resultCodes";
import type { Answer, ResultName, ReviewCase, Tally } from "./data/cases";
import type { QuestionId } from "./data/questions";

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

/** ［判定する］。結果だけを保存する（回答の保存は、別のスライスで扱う）。 */
export async function saveJudgment(caseId: string, result: ResultName): Promise<void> {
  await request(`/api/cases/${encodeURIComponent(caseId)}/judgment`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ result_code: RESULT_CODE_BY_NAME[result] }),
  });
}

/** 画面に出す、失敗の説明。 */
export function describeError(error: unknown): string {
  return error instanceof ApiError ? error.message : "予期しないエラーが発生しました";
}
