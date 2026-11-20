import { useMemo, useState } from "react";
import { AnswerCounts } from "./AnswerCounts";
import { StatusBadge } from "./Badges";
import { createSampleCases, describeError } from "./api";
import { caseStatuses, statusOf, type CaseStatus } from "./data/cases";
import { detailHref } from "./route";
import { useCaseList } from "./useApi";

const normalize = (s: string) => s.normalize("NFKC").toLowerCase();

export type ListFilter = { query: string; status: CaseStatus | "all" };

// 検索語と状態は App が持ち、詳細から戻っても保持する。
export default function CaseList({ filter, onFilterChange }: { filter: ListFilter; onFilterChange: (f: ListFilter) => void }) {
  const { query, status } = filter;
  const setQuery = (q: string) => onFilterChange({ ...filter, query: q });
  const setStatus = (s: CaseStatus | "all") => onFilterChange({ ...filter, status: s });

  // サンプル案件の作成。成功したら version を進めて、一覧を取り直す（ブラウザーの再読み込みは要らない）。
  const [version, setVersion] = useState(0);
  const [creating, setCreating] = useState(false);
  const [sampleNotice, setSampleNotice] = useState<string | null>(null);
  const [sampleError, setSampleError] = useState<string | null>(null);
  const createSamples = async () => {
    setCreating(true);
    setSampleNotice(null);
    setSampleError(null);
    try {
      const result = await createSampleCases();
      setSampleNotice(
        result.created.length > 0
          ? `サンプル案件を${result.created.length}件作成しました。共有のサンプルは全${result.caseIds.length}件です。`
          : `サンプル案件は作成済みです（全${result.caseIds.length}件）。新しく作成した案件はありません。`,
      );
      setVersion((v) => v + 1);
    } catch (error) {
      setSampleError(describeError(error));
    } finally {
      setCreating(false);
    }
  };

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
        <h2 id="list-title" tabIndex={-1}>審査対象一覧</h2>
        <p className="lead">受領したアカウント乗っ取りの申告を確認します。</p>
        <div className="sample-action">
          <button type="button" className="btn" data-testid="create-samples" disabled={creating} aria-busy={creating} onClick={createSamples}>
            {creating ? "作成中…" : "サンプル案件を作成"}
          </button>
          <p className="hint">架空の4件を、利用者全員で共有します。作成済みの場合は、増えません。</p>
          {sampleNotice !== null && (
            <p className="notice-ok" data-testid="sample-notice" aria-live="polite">{sampleNotice}</p>
          )}
          {sampleError !== null && (
            <div className="error sample-error" role="alert" data-testid="sample-error">
              <p className="empty-title">サンプル案件を作成できませんでした</p>
              <p>{sampleError}</p>
              <p>作成済みの案件は残っています。もう一度「サンプル案件を作成」を押して、再試行できます。</p>
            </div>
          )}
        </div>
      </div>

      {remote.status === "loading" && (
        <p className="count" role="status" data-testid="list-loading">読み込み中…</p>
      )}
      {remote.status === "error" && (
        <div className="error" role="alert" data-testid="list-error">
          <p className="empty-title">案件の一覧を取得できませんでした</p>
          <p>{describeError(remote.error)}</p>
        </div>
      )}
      {remote.status === "ok" && (<>
      <div className="toolbar" role="search">
        <label className="sr-only" htmlFor="case-search">案件ID・ユーザーID・申告内容で検索</label>
        <input
          id="case-search"
          className="search"
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="案件ID・ユーザーID・申告内容"
        />
        <label className="sr-only" htmlFor="case-status">状態</label>
        <select
          id="case-status"
          value={status}
          onChange={(e) => setStatus(e.target.value as CaseStatus | "all")}
        >
          <option value="all">すべての状態</option>
          {caseStatuses.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <p className="count" role="status">
          {filtered.length}件を表示（全{cases.length}件）
        </p>
      </div>

      {filtered.length === 0 ? (
        <div className="empty" data-testid="empty-list">
          <p className="empty-title">条件に合う案件がありません</p>
          <p>検索語を変えるか、状態の絞り込みを解除してください。</p>
          {filtering && (
            <button type="button" className="btn" onClick={reset}>条件をクリア</button>
          )}
        </div>
      ) : (
        <div className="table-wrap">
          <table className="case-table" role="table">
            <thead role="rowgroup">
              <tr role="row">
                <th scope="col" role="columnheader">案件</th>
                <th scope="col" role="columnheader">対象</th>
                <th scope="col" role="columnheader">申告内容</th>
                <th scope="col" role="columnheader">状態</th>
                <th scope="col" role="columnheader">操作</th>
              </tr>
            </thead>
            <tbody role="rowgroup">
              {filtered.map((c) => (
                <tr key={c.caseId} className="case-row" role="row">
                  <th scope="row" role="rowheader" className="case-id" data-label="案件">{c.caseId}</th>
                  <td role="cell" data-label="対象">user_id {c.userId}</td>
                  <td role="cell" data-label="申告内容">
                    <span className="case-summary">{c.summary}</span>
                    <AnswerCounts counts={c.counts} />
                  </td>
                  <td role="cell" data-label="状態"><StatusBadge status={statusOf(c)} /></td>
                  <td role="cell" className="op">
                    <a className="btn open" href={detailHref(c.caseId)}>
                      審査を開く<span className="sr-only">（{c.caseId}）</span>
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
