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
        <h1><span className="sr-only">不正審査デモ </span>審査台</h1>
        <span className="tag">画面案（未承認） ／ 架空案件</span>
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
