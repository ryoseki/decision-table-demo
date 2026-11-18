// 設問の追加案（管理者AIの試作。モック・DBなし）のAPI呼び出し。Reactに依存しない。
// 失敗は ApiError にそろえ、画面に出す文言（message）を、原因ごとに変える。
import { ApiError, proposalRequestId, signedBody } from "./api";
import type { Answer } from "./data/cases";
import { toSource, type GenerationSource } from "./generationSource";
import { resultNameOf } from "./resultCodes";

/** 廃止した設問は、一覧に残り、retired が true になる（設問文は保持）。 */
export type DemoQuestion = { id: string; text: string; retired?: boolean };
export type DemoQuestionList = { revision: number; questions: DemoQuestion[]; source: GenerationSource; persistence?: "database"; templateReuse?: boolean };
/** 追加案。kind は持たない（既存の形のまま）。 */
export type AddProposal = {
  kind?: undefined;
  proposalId: string;
  before: DemoQuestion[];
  after: DemoQuestion[];
  addedId: string;
  /** この提案を作った生成元（モックか、実接続か）。 */
  source: GenerationSource;
};
/** 廃止案の、参照しているルール（現行の最新の版）。 */
export type RuleReference = { ruleId: string; revision: number; result: string };
/** 廃止案の、影響を受ける既存案件。answer が null なら未回答。 */
export type ImpactCase = { caseId: string; summary: string; answer: Answer | null; result?: string };
export type RetireProposal = {
  kind: "retire";
  proposalId: string;
  before: DemoQuestion[];
  after: DemoQuestion[];
  retiredId: string;
  retiredText: string;
  source: GenerationSource;
  references: RuleReference[];
  /** 参照が0件のときだけ true。false なら、Yesでも廃止されない。 */
  canRetire: boolean;
  impact: { unjudged: ImpactCase[]; judged: ImpactCase[] };
};
export type QuestionProposal = AddProposal | RetireProposal;
export type Decision = "yes" | "no";
export type DecisionResult = DemoQuestionList & { applied: boolean };

// 一覧を変えない操作（取得・提案の作成）の通信エラー。
const NETWORK_UNCHANGED = "サーバーに接続できませんでした。デモの設問一覧は変わっていません。";
// 決定（Yes／No）の通信エラー。要求がサーバーに届いたか分からないので、反映の有無を言い切らない。
const NETWORK_UNKNOWN =
  "サーバーに接続できませんでした。反映されたかどうかを確認できていません。もう一度送るか、一覧を再読み込みして確認してください。";

export async function call(
  path: string,
  init?: RequestInit,
  networkMessage = NETWORK_UNCHANGED,
  subject = "デモの設問一覧",
  proposalName = "追加案",
): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(path, await signedBody(init));
  } catch {
    throw new ApiError(0, networkMessage);
  }
  if (!response.ok) {
    let detail = "";
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // 本文がJSONでない場合は、詳細なしで扱う。
    }
    throw new ApiError(response.status, errorMessage(response.status, detail, subject, proposalName));
  }
  return response.json();
}

function errorMessage(status: number, detail: string, subject: string, proposalName: string): string {
  const suffix = detail === "" ? "" : `（${detail}）`;
  switch (status) {
    case 422:
      return `AIの応答が検証で拒否されました${suffix}。${subject}は変わっていません。`;
    case 504:
      return `AIの応答がタイムアウトしました${suffix}。${subject}は変わっていません。時間をおいて、もう一度試してください。`;
    case 502:
      return `AIの応答を得られませんでした${suffix}。${subject}は変わっていません。`;
    case 429:
      return detail || "今日のAI提案の利用上限に達しました。";
    case 409:
      if (detail.includes("この依頼は送信済みです")) return detail;
      return `${detail || `${subject}が変わっていたため、反映しませんでした`}。最新の一覧を表示しています。`;
    case 404:
      return `${detail || "提案がありません"}。もう一度、${proposalName}を作ってください。`;
    default:
      return `サーバーがエラーを返しました（HTTP ${status}）。${subject}は変わっていない可能性があります。`;
  }
}

type ApiList = { revision: number; questions: DemoQuestion[]; source?: unknown; persistence?: unknown; template_reuse?: boolean };

const toList = (b: ApiList): DemoQuestionList => ({
  revision: b.revision,
  questions: b.questions,
  source: toSource(b.source),
  ...(b.persistence === "database" ? { persistence: "database" as const } : {}),
  ...(b.template_reuse ? { templateReuse: true } : {}),
});

export async function getDemoQuestions(): Promise<DemoQuestionList> {
  return toList((await call("/api/admin/questions")) as ApiList);
}

export async function createQuestionProposal(prompt: string, requestId = proposalRequestId(), forceAI = false): Promise<QuestionProposal> {
  const b = (await call("/api/admin/question-proposals", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": requestId },
    body: JSON.stringify(forceAI ? { prompt, force_ai: true } : { prompt }),
  })) as ApiProposal;
  if (b.kind === "retire") {
    const toImpact = (c: ApiImpactCase): ImpactCase => ({
      caseId: c.case_id,
      summary: c.summary,
      answer: c.answer,
      ...(c.result_code === undefined ? {} : { result: resultNameOf(c.result_code) }),
    });
    return {
      kind: "retire",
      proposalId: b.proposal_id,
      before: b.before,
      after: b.after,
      retiredId: b.retired.id,
      retiredText: b.retired.text,
      source: toSource(b.source),
      references: b.references.map((r) => ({ ruleId: r.rule_id, revision: r.revision, result: resultNameOf(r.result_code) })),
      canRetire: b.can_retire,
      impact: { unjudged: b.impact.unjudged.map(toImpact), judged: b.impact.judged.map(toImpact) },
    };
  }
  return { proposalId: b.proposal_id, before: b.before, after: b.after, addedId: b.added.id, source: toSource(b.source) };
}

type ApiImpactCase = { case_id: string; summary: string; answer: Answer | null; result_code?: string };
type ApiProposal = { proposal_id: string; before: DemoQuestion[]; after: DemoQuestion[]; source?: unknown } & (
  | { kind?: "add"; added: DemoQuestion }
  | {
      kind: "retire";
      retired: DemoQuestion;
      references: { rule_id: string; revision: number; result_code: string }[];
      can_retire: boolean;
      impact: { unjudged: ApiImpactCase[]; judged: ApiImpactCase[] };
    }
);

export async function decideQuestionProposal(proposalId: string, decision: Decision): Promise<DecisionResult> {
  const b = (await call(`/api/admin/question-proposals/${encodeURIComponent(proposalId)}/decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision }),
  }, NETWORK_UNKNOWN)) as ApiList & { applied: boolean };
  return { ...toList(b), applied: b.applied };
}
