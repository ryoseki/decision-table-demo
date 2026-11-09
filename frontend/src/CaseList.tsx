import { useMemo } from "react";
import { AnswerCounts } from "./AnswerCounts";
import { StatusBadge } from "./Badges";
import { caseStatuses, cases, statusOf, type CaseStatus } from "./data/cases";
import { detailHref } from "./route";

const normalize = (s: string) => s.normalize("NFKC").toLowerCase();

export type ListFilter = { query: string; status: CaseStatus | "all" };

// 検索語と状態は App が持ち、詳細から戻っても保持する。
export default function CaseList({ filter, onFilterChange }: { filter: ListFilter; onFilterChange: (f: ListFilter) => void }) {
  const { query, status } = filter;
  const setQuery = (q: string) => onFilterChange({ ...filter, query: q });
  const setStatus = (s: CaseStatus | "all") => onFilterChange({ ...filter, status: s });

  const filtered = useMemo(() => {
    const words = normalize(query).split(/\s+/).filter((w) => w !== "");
    return cases.filter((c) => {
      if (status !== "all" && statusOf(c) !== status) return false;
      // 案件ID・ユーザーID・申告内容（要約と問い合わせ本文）を対象にする
      const haystack = normalize(`${c.caseId} ${c.userId} user_id ${c.summary} ${c.inquiry}`);
      return words.every((w) => haystack.includes(w));
    });
  }, [query, status]);

  const filtering = query.trim() !== "" || status !== "all";
  const reset = () => onFilterChange({ query: "", status: "all" });

  return (
    <section aria-labelledby="list-title">
      <div className="page-head">
        <h2 id="list-title" tabIndex={-1}>審査対象一覧</h2>
        <p className="lead">
          架空の案件です。案件ID・ユーザーID・申告内容で検索し、状態で絞り込めます。
        </p>
      </div>

      <div className="toolbar" role="search">
        <label className="field search">
          <span className="field-label">検索</span>
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="案件ID・ユーザーID・申告内容（例: CASE-A、456、ログイン）"
          />
        </label>
        <div className="field">
          <span className="field-label" id="status-label">状態</span>
          <div className="chips" role="group" aria-labelledby="status-label">
            {(["all", ...caseStatuses] as const).map((s) => (
              <button
                key={s}
                type="button"
                className="chip"
                aria-pressed={status === s}
                onClick={() => setStatus(s)}
              >
                {s === "all" ? "すべて" : s}
              </button>
            ))}
          </div>
        </div>
      </div>

      <p className="count" role="status" aria-live="polite">
        {filtered.length}件を表示（全{cases.length}件）
      </p>

      {filtered.length === 0 ? (
        <div className="empty" data-testid="empty-list">
          <p className="empty-title">条件に合う案件がありません</p>
          <p>検索語を変えるか、状態の絞り込みを解除してください。</p>
          {filtering && (
            <button type="button" className="btn" onClick={reset}>条件をクリア</button>
          )}
        </div>
      ) : (
        <ul className="case-list">
          {filtered.map((c) => (
            <li key={c.caseId}>
              <a className="case-row" href={detailHref(c.caseId)}>
                <div className="case-main">
                  <div className="case-ids">
                    <span className="case-id">{c.caseId}</span>
                    <span className="user-id">user_id {c.userId}</span>
                  </div>
                  <p className="case-summary">{c.summary}</p>
                </div>
                <div className="case-side">
                  <StatusBadge status={statusOf(c)} />
                  <AnswerCounts c={c} />
                  <span className="btn open">審査を開く</span>
                </div>
              </a>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
