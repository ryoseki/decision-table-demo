import { questions, type QuestionId } from "./questions";

// 固定の架空案件（フロント内のフィクスチャ）。HTTP・DBの契約は未合意のため作らない。
// 回答の値は表示の確認用で、業務上の認定を示すものではない。

/** 回答。確認待ちは No・未回答と区別する。未回答は回答欄に値がないこと。 */
export type Answer = "yes" | "no" | "pending";

/** 画面案の登録結果（定義済みの4種。初版の範囲で合意済みの名称） */
export type ResultName = "アカウント削除" | "アカウント一時凍結" | "注意" | "問題なし";

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
};

export const cases: readonly ReviewCase[] = [
  {
    caseId: "CASE-A",
    userId: "456",
    summary: "心当たりのないログインとクレジット消費",
    inquiry:
      "昨夜のログインに心当たりがなく、今朝クレジットが減っていた。調査資料にはAPIキーへの言及がある。",
    answers: {
      A01: "yes", A02: "yes", A03: "yes", A04: "yes", A05: "yes", A06: "no",
      A07: "yes", A08: "yes", A09: "yes", A10: "no", A11: "yes", A12: "yes",
    },
    autoQuestions: ["A01", "A03", "A09"],
  },
  {
    caseId: "CASE-B",
    userId: "782",
    summary: "身に覚えのないログイン通知",
    inquiry: "知らない端末からログインしたという通知メールが届いた。まだ何も調べていない。",
    answers: {},
  },
  {
    caseId: "CASE-C",
    userId: "219",
    summary: "メールアドレス変更の通知に心当たりがない",
    inquiry: "登録メールアドレスが変わったという通知が届いたが、自分では変更していない。",
    answers: { A01: "yes", A03: "yes" },
  },
  {
    caseId: "CASE-D",
    userId: "903",
    summary: "APIキー発行の通知に心当たりがない",
    inquiry: "新しいAPIキーが発行されたという通知が届いたが、自分では発行していない。",
    answers: { A01: "yes", A03: "no", A05: "no", A07: "yes", A09: "pending" },
    autoQuestions: ["A01", "A03", "A09"],
    pendingReasons: { A09: "APIキー発行履歴の自動回答を取得できませんでした（通信エラー）。" },
  },
  {
    caseId: "CASE-E",
    userId: "118",
    summary: "ログイン失敗の通知が多数届いた",
    inquiry: "ログインに失敗したという通知が夜間に何件も届いた。サインインできている。",
    answers: { A01: "yes", A03: "no" },
    result: "問題なし",
  },
  {
    caseId: "CASE-F",
    userId: "561",
    summary: "APIの利用量が急に増えた",
    inquiry: "APIの利用量が普段より急に増えているので確認してほしい。",
    answers: { A01: "yes", A03: "no", A09: "no" },
    result: "問題なし",
  },
  {
    caseId: "CASE-G",
    userId: "340",
    summary: "パスワード変更の覚えがない",
    inquiry: "パスワードが変更されたと表示されたが、自分では変更した覚えがない。",
    answers: { A01: "no", A02: "no", A05: "pending" },
    pendingReasons: { A05: "アカウント変更履歴を取得できませんでした（通信エラー）。" },
  },
];

export type CaseStatus = "未審査" | "情報取得失敗あり" | "登録済み";
export const caseStatuses: readonly CaseStatus[] = ["未審査", "情報取得失敗あり", "登録済み"];

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

/** 画面案（未承認）の表示用の状態。回答の集計と登録結果の有無から計算する。 */
export function statusOf(c: ReviewCase): CaseStatus {
  if (c.result !== undefined) return "登録済み";
  const t = tally(c);
  if (t.pending > 0) return "情報取得失敗あり";
  return "未審査";
}
