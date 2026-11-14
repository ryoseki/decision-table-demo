// 依存を増やさないため、ハッシュで画面を切り替える。
// #/ は一覧、 #/cases/<案件ID> は詳細、 #/admin/questions は設問管理（デモ）。
export type Route = { name: "list" } | { name: "detail"; caseId: string } | { name: "questions" };

export const QUESTIONS_HREF = "#/admin/questions";

export function parseHash(hash: string): Route {
  if (/^#\/admin\/questions\/?$/.test(hash)) return { name: "questions" };
  const m = /^#\/cases\/([^/]+)\/?$/.exec(hash);
  if (m !== null) {
    try {
      return { name: "detail", caseId: decodeURIComponent(m[1]) };
    } catch {
      return { name: "detail", caseId: m[1] };
    }
  }
  return { name: "list" };
}

export function detailHref(caseId: string): string {
  return `#/cases/${encodeURIComponent(caseId)}`;
}
