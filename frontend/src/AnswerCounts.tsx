import type { Tally } from "./data/cases";

export function AnswerCounts({ counts }: { counts: Tally }) {
  const t = counts;
  return (
    <span className="counts" role="group" aria-label={`Yes ${t.yes}, No ${t.no}, Pending ${t.pending}, Unanswered ${t.unanswered}`}>
      <span className="count-item c-yes">Yes {t.yes}</span>
      <span className="count-item c-no">No {t.no}</span>
      <span className="count-item c-pending">Pending {t.pending}</span>
      <span className="count-item c-none">Unanswered {t.unanswered}</span>
    </span>
  );
}
