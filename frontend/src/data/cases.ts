import { questions, type QuestionId } from "./questions";

// 固定の架空案件。画面は、APIから案件を取得して表示する（api.ts）。この配列は、単体テストの
// フィクスチャとして残している。回答の値は表示の確認用で、業務上の認定を示すものではない。

/** 回答。確認待ちは No・未回答と区別する。未回答は回答欄に値がないこと。 */
export type Answer = "yes" | "no" | "pending";

/** 画面案の登録結果（定義済みの4種。初版の範囲で合意済みの名称） */
export type ResultName = "Account deletion" | "Account suspension" | "Caution" | "No issue";

export type ReviewCase = {
  caseId: string;
  userId: string;
  /** 一覧に出す審査理由の要約 */
  summary: string;
  /** 問い合わせ（申告内容）の架空の本文 */
  inquiry: string;
  answers: Partial<Record<QuestionId, Answer>>;
  /** 表示用フィクスチャ: 「自動」タグを付ける設問。自動回答の対象設問の割り当ては未合意で、業務ルールではない。 */
  autoQuestions?: readonly QuestionId[];
  /** 確認待ちの理由（架空） */
  pendingReasons?: Partial<Record<QuestionId, string>>;
  /** 登録済みの結果。未登録は undefined */
  result?: ResultName;
  /** 採用ルール（参考の記録）。採用していなければ undefined */
  adopted?: AdoptedRule;
  /**
   * 管理者が廃止した設問。新たな回答の対象外で、保存済みの設問文と回答を、読み取り専用で表示する。
   * answers には入れない（回答の集計・照合に使わない）。
   */
  retiredQuestions?: readonly RetiredQuestion[];
};

/** 廃止済みの設問（案件が保存している設問文と回答）。answer がなければ未回答。 */
export type RetiredQuestion = { id: string; prompt: string; answer?: Answer };

/** 採用したルールの版。結果とは独立した、参考の記録。 */
export type AdoptedRule = { ruleId: string; revision: number };

export const cases: readonly ReviewCase[] = [
  {
    caseId: "CASE-A",
    userId: "456",
    summary: "Unrecognized login and credit usage",
    inquiry:
      "I do not recognize last night's login, and my credits had dropped this morning. The investigation materials mention an API key.",
    answers: {
      A01: "yes", A02: "yes", A03: "yes", A04: "yes", A05: "yes", A06: "no",
      A07: "yes", A08: "yes", A09: "yes", A10: "no", A11: "yes", A12: "yes",
    },
    autoQuestions: ["A01", "A03", "A09"],
  },
  {
    caseId: "CASE-B",
    userId: "782",
    summary: "Unrecognized login notification",
    inquiry: "I received an email saying someone logged in from an unknown device. I have not checked anything yet.",
    answers: {},
  },
  {
    caseId: "CASE-C",
    userId: "219",
    summary: "Unrecognized email address change notification",
    inquiry: "I received a notification that my registered email address changed, but I did not change it.",
    answers: { A01: "yes", A03: "yes" },
  },
  {
    caseId: "CASE-D",
    userId: "903",
    summary: "Unrecognized API key issuance notification",
    inquiry: "I received a notification that a new API key was issued, but I did not issue it.",
    answers: { A01: "yes", A03: "no", A05: "no", A07: "yes", A09: "pending" },
    autoQuestions: ["A01", "A03", "A09"],
    pendingReasons: { A09: "Could not retrieve the automatic answer from the API key issuance history (communication error)." },
  },
  {
    caseId: "CASE-E",
    userId: "118",
    summary: "Many failed-login notifications overnight",
    inquiry: "Many notifications of failed logins arrived overnight. I can sign in.",
    answers: { A01: "yes", A03: "no" },
    result: "No issue",
  },
  {
    caseId: "CASE-F",
    userId: "561",
    summary: "API usage suddenly increased",
    inquiry: "API usage has suddenly increased compared with usual. Please check.",
    answers: { A01: "yes", A03: "no", A09: "no" },
    result: "No issue",
  },
  {
    caseId: "CASE-G",
    userId: "340",
    summary: "Unrecognized password change",
    inquiry: "It showed that my password was changed, but I do not recall changing it.",
    answers: { A01: "no", A02: "no", A05: "pending" },
    pendingReasons: { A05: "The account change history could not be retrieved (communication error)." },
  },
];

export type CaseStatus = "Not reviewed" | "Pending data" | "Registered";
export const caseStatuses: readonly CaseStatus[] = ["Not reviewed", "Pending data", "Registered"];

export type Tally = { yes: number; no: number; pending: number; unanswered: number };

export function tally(c: ReviewCase): Tally {
  const t: Tally = { yes: 0, no: 0, pending: 0, unanswered: 0 };
  for (const q of questions) {
    const a = c.answers[q.id];
    if (a === "yes") t.yes += 1;
    else if (a === "no") t.no += 1;
    else if (a === "pending") t.pending += 1;
    else t.unanswered += 1;
  }
  return t;
}

/** 保存済みの回答の表示名。未回答（回答の行がない）は null／undefined。 */
export function answerLabel(answer: Answer | null | undefined): string {
  if (answer === "yes") return "Yes";
  if (answer === "no") return "No";
  if (answer === "pending") return "Pending";
  return "Unanswered";
}

/** 表示用の状態。登録結果の有無と、回答の集計（確認待ちの有無）から計算する。 */
export function statusOf(c: { result?: ResultName; counts: Tally }): CaseStatus {
  if (c.result !== undefined) return "Registered";
  if (c.counts.pending > 0) return "Pending data";
  return "Not reviewed";
}
