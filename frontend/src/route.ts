import { useEffect, useState } from "react";

// 依存を増やさないため、ハッシュで画面を切り替える。 #/ は一覧、 #/cases/<案件ID> は詳細。
export type Route = { name: "list" } | { name: "detail"; caseId: string };

export function parseHash(hash: string): Route {
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

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

export function detailHref(caseId: string): string {
  return `#/cases/${encodeURIComponent(caseId)}`;
}
