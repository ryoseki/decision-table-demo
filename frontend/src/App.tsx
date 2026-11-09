import { useEffect, useRef, useState } from "react";
import CaseDetail from "./CaseDetail";
import CaseList, { type ListFilter } from "./CaseList";
import { useRoute } from "./route";

export default function App() {
  const route = useRoute();
  const [filter, setFilter] = useState<ListFilter>({ query: "", status: "all" });
  const mainRef = useRef<HTMLElement>(null);
  const first = useRef(true);

  // 画面を切り替えたら先頭へ戻し、見出しへフォーカスを移す（初回表示は除く）。
  const key = route.name === "list" ? "list" : route.caseId;
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    window.scrollTo(0, 0);
    mainRef.current?.querySelector<HTMLElement>("h2")?.focus();
  }, [key]);

  return (
    <div className="app">
      <header className="app-header">
        <h1>
          <svg className="logo" viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="4" y="3" width="16" height="18" rx="2.5" /><path d="M8 9h5M8 13h3M12.5 15.5l2 2 3.5-4" /></svg>
          <span className="sr-only">不正審査デモ </span>審査台
        </h1>
        <span className="tag">画面案（未承認） ／ 架空データ</span>
      </header>
      <main ref={mainRef}>
        {route.name === "list" ? (
          <CaseList filter={filter} onFilterChange={setFilter} />
        ) : (
          <CaseDetail key={route.caseId} caseId={route.caseId} />
        )}
      </main>
    </div>
  );
}
