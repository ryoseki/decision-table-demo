import type { Tally } from "./data/cases";

export function AnswerCounts({ counts }: { counts: Tally }) {
  const t = counts;
  return (
    <span className="counts" role="group" aria-label={`Yes ${t.yes}、No ${t.no}、確認待ち ${t.pending}、未回答 ${t.unanswered}`}>
      <span className="count-item c-yes">Yes {t.yes}</span>
      <span className="count-item c-no">No {t.no}</span>
      <span className="count-item c-pending">確認待ち {t.pending}</span>
      <span className="count-item c-none">未回答 {t.unanswered}</span>
    </span>
  );
}
