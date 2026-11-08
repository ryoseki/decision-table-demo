import { tally, type ReviewCase } from "./data/cases";

export function AnswerCounts({ c }: { c: ReviewCase }) {
  const t = tally(c);
  return (
    <span className="counts" role="group" aria-label={`Yes ${t.yes}、No ${t.no}、確認待ち ${t.pending}、未回答 ${t.unanswered}`}>
      <span className="count-item c-yes">Yes {t.yes}</span>
      <span className="count-item c-no">No {t.no}</span>
      <span className="count-item c-pending">確認待ち {t.pending}</span>
      <span className="count-item c-none">未回答 {t.unanswered}</span>
    </span>
  );
}
