// テスト用の補助（fetch のスタブ）。本番のコードからは取り込まない。
import { vi } from "vitest";
import { DEMO_RULES } from "./data/demoRules";
import { RESULT_CODE_BY_NAME } from "./resultCodes";

export type Reply = { status?: number; body?: unknown } | Error;
export type Routes = Record<string, Reply | ((init?: RequestInit) => Reply)>;

/** API のルール一覧（GET /api/rules）。架空のR01〜R03（テスト用の固定の組み合わせ）。 */
export const apiRules = () =>
  DEMO_RULES.map((r) => ({
    rule_id: r.id,
    revision: 1,
    result_code: RESULT_CODE_BY_NAME[r.result],
    conditions: r.expected,
  }));

/**
 * URL（メソッド付き。例: "GET /api/cases"）ごとの応答を返す fetch をスタブにする。
 * "GET /api/rules" は、指定がなければ、架空のR01〜R03を返す（指定すれば、その応答になる）。
 */
export function stubFetch(routes: Routes) {
  routes = { "GET /api/rules": { body: apiRules() }, ...routes };
  const spy = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${String(input)}`;
    const route = routes[key];
    if (route === undefined) throw new Error(`想定外のリクエスト: ${key}`);
    const reply = typeof route === "function" ? route(init) : route;
    if (reply instanceof Error) throw reply;
    const status = reply.status ?? 200;
    return new Response(status === 204 ? null : JSON.stringify(reply.body ?? {}), { status });
  });
  vi.stubGlobal("fetch", spy);
  return spy;
}

/** API の一覧の1行（GET /api/cases）。 */
export const apiRow = (over: Record<string, unknown> = {}) => ({
  case_id: "CASE-X",
  summary: "架空の概要",
  user_id: "456",
  inquiry: { subject: "架空の件名", body: "架空の本文" },
  result_code: null,
  counts: { yes: 1, no: 0, pending: 0, unanswered: 11 },
  ...over,
});

/** API の案件の詳細（GET /api/cases/{id}）。A09 だけ yes、ほかは未回答。 */
export const apiCase = (over: Record<string, unknown> = {}) => ({
  case_id: "CASE-X",
  summary: "架空の概要",
  user: { user_id: "456" },
  inquiry: { subject: "架空の件名", body: "架空の本文" },
  period_start_at: null,
  period_end_at: null,
  questions: Array.from({ length: 12 }, (_, i) => {
    const code = `A${String(i + 1).padStart(2, "0")}`;
    return { question_code: code, answer: code === "A09" ? "yes" : null };
  }),
  result: null,
  ...over,
});
