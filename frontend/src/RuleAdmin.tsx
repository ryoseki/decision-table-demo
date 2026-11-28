import { TemplatePanel } from "./TemplatePanel";
import { useRef, useState } from "react";
import { ApiError, describeError, keepsRequestId, proposalRequestId } from "./api";
import { createRuleProposal, decideRuleProposal, type DemoRuleList, type DemoRuleRow, type RuleProposal } from "./rulesApi";
import { QUESTION_IDS } from "./matching";
import { AiNote, SourceBadge } from "./SourceBadge";
import { useDemoRules } from "./useApi";

type ListState = { status: "loading" } | { status: "ok"; data: DemoRuleList } | { status: "error"; message: string };

const ANSWER_LABEL = { yes: "Yes", no: "No" } as const;

function ConditionText({ rule, addedQuestions = [] }: { rule: DemoRuleRow; addedQuestions?: string[] }) {
  return (
    <>
      {rule.conditions.map((c) => {
        const isAdded = addedQuestions.includes(c.questionId);
        return (
          <span key={c.questionId} className={isAdded ? "condition condition-added" : "condition"} data-added={isAdded ? "true" : undefined}>
            {c.questionId}={ANSWER_LABEL[c.answer]}
            {isAdded && " (added)"}{" "}
          </span>
        );
      })}
    </>
  );
}

/** 提案の種類ごとの呼び名。 */
const KIND_NAME = { add: "add proposal", remove: "remove proposal", update: "update proposal" } as const;

const naturalOrder = (a: string, b: string) => a.localeCompare(b, "en", { numeric: true });

/** 現行の一覧の設問の列。A01〜A12は固定で、A13以降は、条件に出てくるときだけ後ろに自然順で足す。 */
function questionColumns(rules: DemoRuleRow[]): string[] {
  const fixed: string[] = [...QUESTION_IDS];
  const extra = new Set<string>();
  for (const r of rules) for (const c of r.conditions) if (!fixed.includes(c.questionId)) extra.add(c.questionId);
  return [...fixed, ...[...extra].sort(naturalOrder)];
}

function RuleTable({
  title,
  rules,
  addedId,
  removedId,
  updatedId,
  addedQuestions,
  grid = false,
}: {
  title: string;
  rules: DemoRuleRow[];
  /** 現行の一覧: 設問ごとの列に条件を並べる（Before／Afterは条件を1列で出す）。 */
  grid?: boolean;
  /** 追加案で、これから追加するルール（After に「追加」ラベル）。 */
  addedId?: string;
  /** 削除案で、これから削除するルール（Before に「削除」ラベル。After には出ない）。 */
  removedId?: string;
  /** 更新案で、条件を足す対象のルール（Before・After の両方に「更新」ラベル）。 */
  updatedId?: string;
  /** 更新案で、対象のルールに足す条件の設問ID（After で「（追加）」と背景で示す）。 */
  addedQuestions?: string[];
}) {
  const columns = grid ? questionColumns(rules) : [];
  const table = (
      <table className={grid ? "case-table question-table rule-grid" : "case-table question-table"}>
        <thead>
          <tr>
            <th scope="col">Rule</th><th scope="col">Revision</th><th scope="col">Result</th>
            {grid ? columns.map((q) => <th key={q} scope="col">{q}</th>) : <th scope="col">Conditions (question=answer)</th>}
          </tr>
        </thead>
        <tbody>
          {rules.map((r) => {
            const adding = r.ruleId === addedId;
            const removing = r.ruleId === removedId;
            const updating = r.ruleId === updatedId;
            return (
              <tr
                key={r.ruleId}
                className={adding ? "row-added" : removing ? "row-retired" : updating ? "row-updated" : undefined}
                data-testid={`${title}-${r.ruleId}`}
              >
                <th scope="row" className="case-id">
                  {r.ruleId}
                  {adding && <span className="badge added-label">Added</span>}
                  {removing && <span className="badge retired-label">Removed</span>}
                  {updating && <span className="badge updated-label">Updated</span>}
                </th>
                <td>{r.revision}</td>
                <td>{r.result}</td>
                {grid
                  ? columns.map((q) => {
                      const c = r.conditions.find((x) => x.questionId === q);
                      return (
                        <td key={q}>
                          {c ? <span className={`answer-chip answer-${c.answer}`}>{ANSWER_LABEL[c.answer]}</span> : "-"}
                        </td>
                      );
                    })
                  : <td><ConditionText rule={r} addedQuestions={updating ? addedQuestions : undefined} /></td>}
              </tr>
            );
          })}
        </tbody>
      </table>
  );
  return (
    <section className="pane" aria-label={title}>
      <div className="pane-head"><h3>{title}</h3><span>{rules.length} rules</span></div>
      {grid ? (
        <div className="rule-grid-scroll" role="group" tabIndex={0} aria-label={`${title} table (scrolls horizontally)`}>{table}</div>
      ) : (
        table
      )}
    </section>
  );
}

// 判定ルール管理（デモ）。設問管理と同じ流れ（依頼 → 差分 → Yes／No）で、操作の対象が判定ルール。
// AIの応答は、既定はモック（サーバーの設定でAmazon Bedrockの実接続にもなる）。反映先はサーバーのメモリ上のデモのルール一覧（DB・審査画面のルールではない）。
export default function RuleAdmin() {
  const [version, setVersion] = useState(0);
  const remote = useDemoRules(version);
  const [latest, setLatest] = useState<DemoRuleList | null>(null);
  const list: ListState =
    latest !== null
      ? { status: "ok", data: latest }
      : remote.status === "ok"
        ? { status: "ok", data: remote.data }
        : remote.status === "loading"
          ? { status: "loading" }
          : { status: "error", message: describeError(remote.error) };
  const [prompt, setPrompt] = useState("");
  const [proposal, setProposal] = useState<RuleProposal | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const retry = useRef<{ prompt: string; id: string; forceAI: boolean } | null>(null);
  const [proposedPrompt, setProposedPrompt] = useState("");
  const [aiPrompt, setAiPrompt] = useState<string | null>(null);
  // Yesで反映するたびに、サーバーが定型文へ自動登録する。一覧を取得し直すための値。
  const [templateVersion, setTemplateVersion] = useState(0);

  const create = async (forceAI = false, input = prompt) => {
    if (busy) return; // ボタンを無効にしても、フォームの送信は別経路で起きうる。
    setBusy(true);
    setNotice("");
    setError("");
    setProposal(null);
    const previous = retry.current;
    const submission = previous?.prompt === input && previous.forceAI === forceAI
      ? previous
      : { prompt: input, id: proposalRequestId(), forceAI };
    if (!forceAI) setAiPrompt(null);
    retry.current = submission;
    try {
      setProposal(await createRuleProposal(input, submission.id, forceAI));
      setProposedPrompt(input);
      setAiPrompt(null);
      retry.current = null;
    } catch (e) {
      // 到達したか不明な要求（接続できない・503）だけ、同じ入力の再送で同じ依頼IDを使う。
      if (!keepsRequestId(e)) retry.current = null;
      setError(describeError(e));
    } finally {
      setBusy(false);
    }
  };

  const decide = async (decision: "yes" | "no") => {
    if (proposal === null) return;
    setBusy(true);
    setNotice("");
    setError("");
    try {
      const result = await decideRuleProposal(proposal.proposalId, decision);
      setLatest(result);
      if (result.applied) setTemplateVersion((v) => v + 1);
      setAiPrompt(decision === "no" && proposal.source.kind === "template" ? proposedPrompt : null);
      setProposal(null);
      const applied = { add: " added", remove: " removed", update: " conditions updated" }[proposal.kind];
      setNotice(
        result.applied
          ? `Applied to the demo rule list (rule ${proposal.targetId}${applied})`
          : `This ${KIND_NAME[proposal.kind]} was not applied. The list below shows the latest state on the server.`,
      );
    } catch (e) {
      setError(describeError(e));
      // 提案が使えなくなった（未知・使用済み・基準の変更・参照する設問の変更）ときは、閉じて最新の一覧を出す。
      if (e instanceof ApiError && (e.status === 404 || e.status === 409)) {
        setProposal(null);
        setLatest(null);
        setVersion((v) => v + 1);
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-labelledby="rules-title">
      <div className="page-head">
        <h2 id="rules-title" tabIndex={-1}>Rule admin (demo)</h2>
        <p className="lead">Write a request in plain language to create an add, remove, or update proposal (adding conditions to an existing rule), review the before/after difference, and decide whether to apply it.</p>
      </div>
      <p className="target-badge" data-testid="target">Editing: Judgment rules</p>

      <p className="note note-top" data-testid="mock-note">
        <AiNote source={list.status === "ok" ? list.data.source : null} failed={list.status === "error"} />
        {list.status === "ok" && (list.data.persistence === "database" ? (
          <> Approved rules are shared with everyone and are used for matching when a case without a registered judgment is opened.</>
        ) : (
          <> Changes apply only to the demo rule list in server memory. It survives a reload, but returns to its initial state when the backend restarts (not persistent storage).
        The rules used for matching on the review screen, the cases, and the database do not change.</>
        ))}
        The app validates the questions and results a rule references (it rejects nonexistent questions, undefined results, and duplicate rules).
      </p>

      {list.status === "loading" && <p className="count" role="status">Loading…</p>}
      {list.status === "error" && (
        <div className="error" role="alert" data-testid="list-error">
          <p className="empty-title">Could not retrieve the demo rule list</p>
          <p>{list.message}</p>
        </div>
      )}

      <form
        className="proposal-form"
        onSubmit={(e) => {
          e.preventDefault();
          void create();
        }}
      >
        <label htmlFor="rule-prompt"><strong>Change request (plain language)</strong></label>
        <textarea
          id="rule-prompt"
          rows={3}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Example: Add a rule that gives Caution when question A09 is Yes and question A10 is No"
        />
        <p className="hint">Example request to remove a rule: Remove rule R03</p>
        <p className="hint">Example request to add a condition to an existing rule (update): Add condition A09=Yes to R04</p>
        <div>
          <button type="submit" className="btn" disabled={busy || prompt.trim() === ""}>Create proposal</button>
        </div>
      </form>

      {error !== "" && <p className="error-inline" role="alert" data-testid="proposal-error">{error}</p>}
      {notice !== "" && <p className="notice-ok" role="status" data-testid="proposal-notice">{notice}</p>}

      {proposal !== null && (
        <section className="proposal" aria-labelledby="rule-diff-title" data-testid="proposal">
          <h3 id="rule-diff-title">Proposed change: {{ add: "add rule", remove: "remove rule", update: "update rule" }[proposal.kind]} (not applied yet)</h3>
          <SourceBadge source={proposal.source} />
          {proposal.kind === "update" && (
            <p className="update-summary" data-testid="update-summary">
              Rule to update: {proposal.targetId} (ID, existing conditions, and result are unchanged). Conditions to add:{" "}
              {proposal.addedConditions.map((c) => `${c.questionId}=${ANSWER_LABEL[c.answer]}`).join(", ")}
            </p>
          )}
          <div className="diff">
            <RuleTable
              title="Before (current)"
              rules={proposal.before}
              removedId={proposal.kind === "remove" ? proposal.targetId : undefined}
              updatedId={proposal.kind === "update" ? proposal.targetId : undefined}
            />
            <RuleTable
              title="After (if applied)"
              rules={proposal.after}
              addedId={proposal.kind === "add" ? proposal.targetId : undefined}
              updatedId={proposal.kind === "update" ? proposal.targetId : undefined}
              addedQuestions={proposal.addedConditions.map((c) => c.questionId)}
            />
          </div>
          <p className="confirm-q" id="rule-confirm-q"><strong>Apply this change?</strong></p>
          <div className="confirm-actions" role="group" aria-labelledby="rule-confirm-q">
            <button type="button" className="btn" disabled={busy} onClick={() => void decide("yes")}>Yes</button>
            <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => void decide("no")}>No</button>
          </div>
        </section>
      )}

      {list.status === "ok" && (
        <div className="current-list">
          <RuleTable title="Current demo rule list" rules={list.data.rules} grid />
        </div>
      )}
      {aiPrompt !== null && (
        <button type="button" className="btn ghost" disabled={busy} onClick={() => void create(true, aiPrompt)}>
          Re-propose with AI
        </button>
      )}
      {list.status === "ok" && list.data.templateReuse && (
        <TemplatePanel target="rule" refreshKey={templateVersion} />
      )}
    </section>
  );
}
