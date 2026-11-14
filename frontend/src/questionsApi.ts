// 設問の追加案（管理者AIの試作。モック・DBなし）のAPI呼び出し。Reactに依存しない。
// 失敗は ApiError にそろえ、画面に出す文言（message）を、原因ごとに変える。
import { ApiError } from "./api";

export type DemoQuestion = { id: string; text: string };
export type DemoQuestionList = { revision: number; questions: DemoQuestion[] };
export type QuestionProposal = {
  proposalId: string;
  before: DemoQuestion[];
  after: DemoQuestion[];
  addedId: string;
};
export type Decision = "yes" | "no";
export type DecisionResult = DemoQuestionList & { applied: boolean };

// 一覧を変えない操作（取得・提案の作成）の通信エラー。
const NETWORK_UNCHANGED = "サーバーに接続できませんでした。デモの設問一覧は変わっていません。";
// 決定（Yes／No）の通信エラー。要求がサーバーに届いたか分からないので、反映の有無を言い切らない。
const NETWORK_UNKNOWN =
  "サーバーに接続できませんでした。反映されたかどうかを確認できていません。もう一度送るか、一覧を再読み込みして確認してください。";

async function call(path: string, init?: RequestInit, networkMessage = NETWORK_UNCHANGED): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(path, init);
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
    throw new ApiError(response.status, errorMessage(response.status, detail));
  }
  return response.json();
}

function errorMessage(status: number, detail: string): string {
  const suffix = detail === "" ? "" : `（${detail}）`;
  switch (status) {
    case 422:
      return `AIの応答（モック）が検証で拒否されました${suffix}。デモの設問一覧は変わっていません。`;
    case 502:
      return `AIの応答を得られませんでした${suffix}。デモの設問一覧は変わっていません。`;
    case 409:
      return `${detail || "デモの設問一覧が変わっていたため、反映しませんでした"}。最新の一覧を表示しています。`;
    case 404:
      return `${detail || "提案がありません"}。もう一度、追加案を作ってください。`;
    default:
      return `サーバーがエラーを返しました（HTTP ${status}）。デモの設問一覧は変わっていない可能性があります。`;
  }
}

type ApiList = { revision: number; questions: DemoQuestion[] };

const toList = (b: ApiList): DemoQuestionList => ({ revision: b.revision, questions: b.questions });

export async function getDemoQuestions(): Promise<DemoQuestionList> {
  return toList((await call("/api/admin/questions")) as ApiList);
}

export async function createQuestionProposal(prompt: string): Promise<QuestionProposal> {
  const b = (await call("/api/admin/question-proposals", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt }),
  })) as { proposal_id: string; before: DemoQuestion[]; after: DemoQuestion[]; added: DemoQuestion };
  return { proposalId: b.proposal_id, before: b.before, after: b.after, addedId: b.added.id };
}

export async function decideQuestionProposal(proposalId: string, decision: Decision): Promise<DecisionResult> {
  const b = (await call(`/api/admin/question-proposals/${encodeURIComponent(proposalId)}/decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision }),
  }, NETWORK_UNKNOWN)) as ApiList & { applied: boolean };
  return { ...toList(b), applied: b.applied };
}
