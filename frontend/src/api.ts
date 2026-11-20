// APIの呼び出し（画面は相対パス /api/... を呼ぶ。開発とE2EではViteが中継し、本番は同じオリジン）。
// Reactに依存しない。通信の失敗は ApiError にそろえる（status 0 は、通信できなかった場合）。
import { RESULT_CODE_BY_NAME, resultNameOf } from "./resultCodes";
import type { AdoptedRule, Answer, ResultName, RetiredQuestion, ReviewCase, Tally } from "./data/cases";
import type { QuestionId } from "./data/questions";
import type { Rule } from "./matching";

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

/** OACには実際に送るJSON文字列のUTF-8バイト列のハッシュを渡す。 */
export async function signedBody(init?: RequestInit): Promise<RequestInit | undefined> {
  if ((init?.method !== "POST" && init?.method !== "PUT") || typeof init.body !== "string") {
    return init;
  }
  if (!globalThis.crypto?.subtle && import.meta.env.DEV) return init;
  const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(init.body));
  const headers = new Headers(init.headers);
  headers.set("x-amz-content-sha256", Array.from(new Uint8Array(hash), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join(""));
  return { ...init, headers };
}

export function proposalRequestId(): string {
  // getRandomValues は開発用HTTPからも利用できる。
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join("-");
}

/**
 * 応答を確認できない失敗（接続できない・503）だけ、再送で同じ依頼IDを使う。サーバーが
 * 結果を返した失敗（502・504などの生成の失敗、4xx）は、その依頼で枠を使い切っているので、
 * 次の送信は新しい依頼IDにする。
 */
export function keepsRequestId(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 0 || error.status === 503);
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(path, init?.body == null ? init : await signedBody(init));
  } catch {
    throw new ApiError(0, "サーバーに接続できませんでした");
  }
  if (response.status === 503) {
    throw new ApiError(503, "サーバーが混み合っています。しばらくしてから、もう一度お試しください（HTTP 503）");
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

/** 共有サンプル案件の作成結果。caseId は、その操作で決まる案件ID。created は、今回新しく作ったか（再送で作成済みなら false）。 */
export type SampleCaseResult = { caseId: string; created: boolean };

/**
 * 共有の架空サンプル案件を、操作1回につき1件作る（POST /api/sample-cases）。
 * operationId は、その操作を表す依頼ID（UUID。Idempotency-Key）。同じ依頼IDの再送・失敗後の再試行は、
 * 同じ案件になり、重複しない（created が false）。別の依頼IDは、別の案件を1件足す。
 * 通信できない・503・応答を読み取れない失敗でも、作成の結果が分からないので、画面は同じ依頼IDで再試行する。
 */
export async function createSampleCase(operationId: string): Promise<SampleCaseResult> {
  const response = await request("/api/sample-cases", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": operationId },
    body: "{}",
  });
  try {
    const r = (await response.json()) as { case_id: unknown; created: unknown };
    if (typeof r.case_id !== "string" || typeof r.created !== "boolean") throw new Error("形式が違う");
    return { caseId: r.case_id, created: r.created };
  } catch {
    throw new ApiError(0, "サーバーの応答を読み取れませんでした");
  }
}

type ApiCase = {
  case_id: string;
  summary: string;
  user: { user_id: string };
  inquiry: { subject: string; body: string };
  questions: { question_code: string; prompt?: string; answer: Answer | null; retired?: boolean }[];
  result: { result_code: string; adopted_rule: { rule_id: string; rule_revision: number } | null } | null;
};

/** 案件の詳細。未回答（回答の行がない）は、answers に入れない。 */
export async function getCase(caseId: string): Promise<ReviewCase> {
  const c = (await (await request(`/api/cases/${encodeURIComponent(caseId)}`)).json()) as ApiCase;
  const answers: ReviewCase["answers"] = {};
  const retiredQuestions: RetiredQuestion[] = [];
  for (const q of c.questions) {
    if (q.retired === true) {
      // 廃止済みの設問は、回答の対象にしない。保存済みの設問文と回答だけ、別に持つ。
      retiredQuestions.push({
        id: q.question_code,
        prompt: q.prompt ?? "",
        ...(q.answer === null ? {} : { answer: q.answer }),
      });
    } else if (q.answer !== null) {
      answers[q.question_code as QuestionId] = q.answer;
    }
  }
  return {
    caseId: c.case_id,
    userId: c.user.user_id,
    summary: c.summary,
    inquiry: c.inquiry.body,
    answers,
    ...(retiredQuestions.length === 0 ? {} : { retiredQuestions }),
    ...(c.result === null ? {} : { result: resultNameOf(c.result.result_code) }),
    ...(c.result?.adopted_rule == null
      ? {}
      : { adopted: { ruleId: c.result.adopted_rule.rule_id, revision: c.result.adopted_rule.rule_revision } }),
  };
}

type ApiRule = {
  rule_id: string;
  revision: number;
  result_code: string;
  conditions: Record<string, string>;
};

/** APIのルールを、照合用の形にする。 */
function toRule(r: ApiRule): Rule {
  // 条件のある設問だけを持つ。ない設問は「問わない」。条件の値は yes／no だけ、設問IDは共有設定で追加される。
  const expected: Partial<Record<QuestionId, "yes" | "no">> = {};
  for (const [q, value] of Object.entries(r.conditions)) {
    if (!/^A[0-9]{2,}$/.test(q)) {
      throw new Error(`ルール${r.rule_id}の条件に、未知の設問${q}があります`);
    }
    if (value !== "yes" && value !== "no") {
      throw new Error(`ルール${r.rule_id}の条件${q}が、yes／no ではありません`);
    }
    expected[q as QuestionId] = value;
  }
  return { id: r.rule_id, revision: r.revision, result: resultNameOf(r.result_code), expected };
}

/** 照合に使うルール。APIが返す順（rule_id順）のまま。 */
export async function listRules(): Promise<Rule[]> {
  const rows = (await (await request("/api/rules")).json()) as ApiRule[];
  return rows.map(toRule);
}

/** 人が選んだ回答（Yes・No）。確認待ち・未回答に戻す操作はない。 */
export type HumanAnswers = Partial<Record<QuestionId, "yes" | "no">>;

/**
 * ［判定する］。結果と、**変更した回答だけ**を保存する（変更がなければ answers は送らない）。
 * 採用ルールは、採用しているときだけ rule_id と rule_revision を送る（結果を選んだだけでは送らない）。
 */
export async function saveJudgment(
  caseId: string,
  result: ResultName,
  answers: HumanAnswers = {},
  adopted: AdoptedRule | null = null,
): Promise<void> {
  const body: { result_code: string; answers?: HumanAnswers; rule_id?: string; rule_revision?: number } = {
    result_code: RESULT_CODE_BY_NAME[result],
  };
  if (Object.keys(answers).length > 0) body.answers = answers;
  if (adopted !== null) {
    body.rule_id = adopted.ruleId;
    body.rule_revision = adopted.revision;
  }
  try {
    await request(`/api/cases/${encodeURIComponent(caseId)}/judgment`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (error) {
    if (error instanceof ApiError && error.status === 503) {
      // 保存先の衝突で、サーバー側の再試行を使い切った場合など。入力は画面に残っている。
      throw new ApiError(503, `${error.message.replace("（HTTP 503）", "")}入力は残っています。（HTTP 503）`);
    }
    throw error;
  }
}

/** 画面に出す、失敗の説明。 */
export function describeError(error: unknown): string {
  return error instanceof ApiError ? error.message : "予期しないエラーが発生しました";
}
