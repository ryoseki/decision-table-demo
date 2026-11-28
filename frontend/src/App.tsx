import { useEffect, useRef, useState } from "react";
import CaseDetail from "./CaseDetail";
import CaseList, { type ListFilter } from "./CaseList";
import QuestionAdmin from "./QuestionAdmin";
import RuleAdmin from "./RuleAdmin";
import { QUESTIONS_HREF, RULES_HREF } from "./route";
import { useSampleCreation } from "./useApi";
import { useRoute } from "./useRoute";

export default function App() {
  const route = useRoute();
  const [filter, setFilter] = useState<ListFilter>({ query: "", status: "all" });
  const sample = useSampleCreation(); // 一覧を離れても、未完了のサンプル作成の操作を保つ
  const mainRef = useRef<HTMLElement>(null);
  const first = useRef(true);

  // 画面を切り替えたら先頭へ戻し、見出しへフォーカスを移す（初回表示は除く）。
  const key = route.name === "detail" ? route.caseId : route.name;
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
          <span className="sr-only">Fraud Review Demo </span>Review Desk
        </h1>
        <nav className="app-nav" aria-label="Menu">
          <a href="#/">Cases</a>
          <a href={QUESTIONS_HREF}>Question admin</a>
          <a href={RULES_HREF}>Rule admin</a>
        </nav>
        <span className="tag">Draft screens (unapproved) / Fictional data</span>
      </header>
      <main ref={mainRef}>
        {route.name === "list" && <CaseList filter={filter} onFilterChange={setFilter} sample={sample} />}
        {route.name === "detail" && <CaseDetail key={route.caseId} caseId={route.caseId} />}
        {route.name === "questions" && <QuestionAdmin />}
        {route.name === "rules" && <RuleAdmin />}
      </main>
    </div>
  );
}
