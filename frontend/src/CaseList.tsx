import { useMemo } from "react";
import { AnswerCounts } from "./AnswerCounts";
import { StatusBadge } from "./Badges";
import { describeError } from "./api";
import { caseStatuses, statusOf, type CaseStatus } from "./data/cases";
import { detailHref } from "./route";
import { useCaseList, type SampleCreation } from "./useApi";

const normalize = (s: string) => s.normalize("NFKC").toLowerCase();

export type ListFilter = { query: string; status: CaseStatus | "all" };

// 検索語と状態は App が持ち、詳細から戻っても保持する。
export default function CaseList({
  filter,
  onFilterChange,
  sample,
}: {
  filter: ListFilter;
  onFilterChange: (f: ListFilter) => void;
  sample: SampleCreation;
}) {
  const { query, status } = filter;
  const setQuery = (q: string) => onFilterChange({ ...filter, query: q });
  const setStatus = (s: CaseStatus | "all") => onFilterChange({ ...filter, status: s });

  // サンプル案件の作成の状態は App が持つ（画面を切り替えても、未完了の操作を失わない）。
  // 成功したら version が進み、一覧を取り直す（ブラウザーの再読み込みは要らない）。
  const { version, creating, notice: sampleNotice, noticeCaseId, error: sampleError, create: createSample } = sample;

  const remote = useCaseList(version);
  const cases = useMemo(() => (remote.status === "ok" ? remote.data : []), [remote]);

  const filtered = useMemo(() => {
    const words = normalize(query).split(/\s+/).filter((w) => w !== "");
    return cases.filter((c) => {
      if (status !== "all" && statusOf(c) !== status) return false;
      // 案件ID・ユーザーID・申告内容（要約と問い合わせ本文）を対象にする
      const haystack = normalize(`${c.caseId} ${c.userId} user_id ${c.summary} ${c.inquiry}`);
      return words.every((w) => haystack.includes(w));
    });
  }, [cases, query, status]);

  const filtering = query.trim() !== "" || status !== "all";
  const reset = () => onFilterChange({ query: "", status: "all" });

  return (
    <section aria-labelledby="list-title">
      <div className="page-head">
        <h2 id="list-title" tabIndex={-1}>Cases to review</h2>
        <p className="lead">Review the account issues reported by users.</p>
        <div className="sample-action">
          <button type="button" className="btn" data-testid="create-samples" disabled={creating} aria-busy={creating} onClick={() => createSample()}>
            {creating ? "Creating…" : "Create sample case"}
          </button>
          <p className="hint">Each click adds one fictional case. Cases are shared by all users.</p>
          {sampleNotice !== null && (
            <p className="notice-ok" data-testid="sample-notice" aria-live="polite" title={noticeCaseId ?? undefined}>{sampleNotice}</p>
          )}
          {sampleError !== null && (
            <div className="error sample-error" role="alert" data-testid="sample-error">
              <p className="empty-title">Could not create the sample case</p>
              <p>{sampleError}</p>
              <p>Press "Create sample case" again to retry. It retries the same operation, so the case will not be duplicated.</p>
            </div>
          )}
        </div>
      </div>

      {remote.status === "loading" && (
        <p className="count" role="status" data-testid="list-loading">Loading…</p>
      )}
      {remote.status === "error" && (
        <div className="error" role="alert" data-testid="list-error">
          <p className="empty-title">Could not retrieve the case list</p>
          <p>{describeError(remote.error)}</p>
        </div>
      )}
      {remote.status === "ok" && (<>
      <div className="toolbar" role="search">
        <label className="sr-only" htmlFor="case-search">Search by case ID, user ID, or reported issue</label>
        <input
          id="case-search"
          className="search"
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Case ID / user ID / reported issue"
        />
        <label className="sr-only" htmlFor="case-status">Status</label>
        <select
          id="case-status"
          value={status}
          onChange={(e) => setStatus(e.target.value as CaseStatus | "all")}
        >
          <option value="all">All statuses</option>
          {caseStatuses.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <p className="count" role="status">
          Showing {filtered.length} of {cases.length} cases
        </p>
      </div>

      {filtered.length === 0 ? (
        <div className="empty" data-testid="empty-list">
          <p className="empty-title">No cases match the conditions</p>
          <p>Change the search words or clear the status filter.</p>
          {filtering && (
            <button type="button" className="btn" onClick={reset}>Clear filters</button>
          )}
        </div>
      ) : (
        <div className="table-wrap">
          <table className="case-table" role="table">
            <thead role="rowgroup">
              <tr role="row">
                <th scope="col" role="columnheader">Case</th>
                <th scope="col" role="columnheader">User</th>
                <th scope="col" role="columnheader">Reported issue</th>
                <th scope="col" role="columnheader">Status</th>
                <th scope="col" role="columnheader">Action</th>
              </tr>
            </thead>
            <tbody role="rowgroup">
              {filtered.map((c) => (
                <tr key={c.caseId} className="case-row" role="row">
                  <th scope="row" role="rowheader" className="case-id" data-label="Case" title={c.caseId}>{c.caseId}</th>
                  <td role="cell" data-label="User">user_id {c.userId}</td>
                  <td role="cell" data-label="Reported issue">
                    <span className="case-summary">{c.summary}</span>
                    <AnswerCounts counts={c.counts} />
                  </td>
                  <td role="cell" data-label="Status"><StatusBadge status={statusOf(c)} /></td>
                  <td role="cell" className="op">
                    <a className="btn open" href={detailHref(c.caseId)}>
                      Open review <span className="sr-only">({c.caseId})</span>
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      </>)}
    </section>
  );
}
