import { TemplatePanel } from "./TemplatePanel";
import { useRef, useState } from "react";
import { ApiError, describeError, keepsRequestId, proposalRequestId } from "./api";
import { answerLabel } from "./data/cases";
import {
  createQuestionProposal,
  decideQuestionProposal,
  type DemoQuestion,
  type DemoQuestionList,
  type ImpactCase,
  type QuestionProposal,
  type RetireProposal,
} from "./questionsApi";
import { detailHref } from "./route";
import { AiNote, SourceBadge } from "./SourceBadge";
import { useDemoQuestions } from "./useApi";

type ListState = { status: "loading" } | { status: "ok"; data: DemoQuestionList } | { status: "error"; message: string };

function QuestionTable({
  title,
  questions,
  addedId,
  retiredId,
}: {
  title: string;
  questions: DemoQuestion[];
  addedId?: string;
  /** 廃止案で、これから廃止する設問（プレビューの After に「廃止」ラベルを付ける）。 */
  retiredId?: string;
}) {
  return (
    <section className="pane" aria-label={title}>
      <div className="pane-head"><h3>{title}</h3><span>{questions.length} questions</span></div>
      <table className="case-table question-table">
        <thead><tr><th scope="col">ID</th><th scope="col">Question</th></tr></thead>
        <tbody>
          {questions.map((q) => {
            const retiring = q.id === retiredId;
            const rowClass = q.id === addedId ? "row-added" : retiring || q.retired === true ? "row-retired" : undefined;
            return (
              <tr key={q.id} className={rowClass} data-testid={`${title}-${q.id}`}>
                <th scope="row" className="case-id">{q.id}</th>
                <td>
                  {q.text}
                  {q.id === addedId && <span className="badge added-label">Added</span>}
                  {retiring && <span className="badge retired-label">Deactivating</span>}
                  {!retiring && q.retired === true && <span className="badge retired-label">Deactivated</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

function CaseImpactList({ title, cases, note }: { title: string; cases: ImpactCase[]; note: string }) {
  return (
    <div>
      <h5>{title} ({cases.length})</h5>
      <p className="hint">{note}</p>
      {cases.length > 0 && (
        <ul>
          {cases.map((c) => (
            <li key={c.caseId} data-testid={`impact-${c.caseId}`}>
              <a href={detailHref(c.caseId)} title={c.caseId}>{c.caseId}</a> {c.summary}
              <span className="impact-detail"> Saved answer: {answerLabel(c.answer)}</span>
              {c.result !== undefined && <span className="impact-detail"> Result at registration: {c.result}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// 廃止案の差分の補足。現行ルールの参照先と、既存案件への影響を示す（何も変更しない）。
function RetirementDetail({ proposal }: { proposal: RetireProposal }) {
  const { references, impact } = proposal;
  return (
    <div className="retirement" data-testid="retirement-detail">
      <section aria-label="Rules that reference it">
        <h4>Rules that reference it</h4>
        {references.length === 0 ? (
          <p>No current rule references question {proposal.retiredId}.</p>
        ) : (
          <>
            <table className="case-table">
              <thead><tr><th scope="col">Rule</th><th scope="col">Revision</th><th scope="col">Result</th></tr></thead>
              <tbody>
                {references.map((r) => (
                  <tr key={r.ruleId} data-testid={`reference-${r.ruleId}`}>
                    <th scope="row" className="case-id">{r.ruleId}</th>
                    <td>{r.revision}</td>
                    <td>{r.result}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="warn" role="alert" data-testid="retire-blocked">
              {references.length} {references.length === 1 ? "rule references" : "rules reference"} question {proposal.retiredId}, so choosing Yes will not deactivate it (rules are not changed).
            </p>
          </>
        )}
      </section>
      <section aria-label="Impact on existing cases">
        <h4>Impact on existing cases</h4>
        <CaseImpactList
          title="Cases without a registered judgment"
          cases={impact.unjudged}
          note="Matching uses the latest rules. Deactivated questions are no longer open to new answers. The saved question text and answers remain visible on the review screen."
        />
        <CaseImpactList
          title="Cases with a registered judgment"
          cases={impact.judged}
          note="Shows the result at registration and the saved question text and answers. Deactivated questions cannot be answered anew."
        />
      </section>
      <p className="note" data-testid="no-cascade">
        This operation does not change the saved rules or cases (no cascading changes).
      </p>
    </div>
  );
}

// 設問管理（デモ）。AIの応答は、既定はモック（サーバーの設定でAmazon Bedrockの実接続にもなり、生成元は画面に出す）。反映先はサーバーのメモリ上のデモ一覧（DBではない）。
// 追加案のプレビューは、この画面の状態だけに持つ。再読み込みすると破棄される（一覧は変わらない）。
export default function QuestionAdmin() {
  // 一覧は、サーバーから取得したもの（remote）。Yes・Noの応答で最新が分かったときは、それを優先する（latest）。
  const [version, setVersion] = useState(0);
  const remote = useDemoQuestions(version);
  const [latest, setLatest] = useState<DemoQuestionList | null>(null);
  const list: ListState =
    latest !== null
      ? { status: "ok", data: latest }
      : remote.status === "ok"
        ? { status: "ok", data: remote.data }
        : remote.status === "loading"
          ? { status: "loading" }
          : { status: "error", message: describeError(remote.error) };
  const [prompt, setPrompt] = useState("");
  const [proposal, setProposal] = useState<QuestionProposal | null>(null);
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
      setProposal(await createQuestionProposal(input, submission.id, forceAI));
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
    if (proposal === null || busy) return;
    setBusy(true);
    setNotice("");
    setError("");
    try {
      const result = await decideQuestionProposal(proposal.proposalId, decision);
      setLatest(result);
      if (result.applied) setTemplateVersion((v) => v + 1);
      setAiPrompt(decision === "no" && proposal.source.kind === "template" ? proposedPrompt : null);
      setProposal(null);
      const retiring = proposal.kind === "retire";
      setNotice(
        result.applied
          ? retiring
            ? `Applied to the demo question list (question ${proposal.retiredId} deactivated; saved question text and answers remain)`
            : "Applied to the demo question list"
          : `This ${retiring ? "deactivate proposal" : "add proposal"} was not applied. The list below shows the latest state on the server.`,
      );
    } catch (e) {
      setError(describeError(e));
      // 提案が使えなくなった（未知・使用済み・基準の変更）ときは、プレビューを閉じて最新の一覧を出す。
      if (e instanceof ApiError && (e.status === 404 || e.status === 409)) {
        setProposal(null);
        setLatest(null);
        setVersion((v) => v + 1); // 最新の一覧を取り直す
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-labelledby="questions-title">
      <div className="page-head">
        <h2 id="questions-title" tabIndex={-1}>Question admin (demo)</h2>
        <p className="lead">Write a request in plain language to create an add or deactivate proposal, review the before/after difference, and decide whether to apply it.</p>
      </div>

      <p className="target-badge" data-testid="target">Editing: Questions</p>

      <p className="note note-top" data-testid="mock-note">
        <AiNote source={list.status === "ok" ? list.data.source : null} failed={list.status === "error"} />
        {list.status === "ok" && (list.data.persistence === "database" ? (
          <> Approved questions are saved in the shared database and are visible to other visitors.</>
        ) : (
          <> Changes apply only to the demo list in server memory. It survives a reload, but returns to its initial state when the backend restarts (not persistent storage).
        The case questions, answers, and the database do not change.</>
        ))}
        When a question is deactivated, it is no longer open to new answers on the case screen (the saved question text and answers remain).
      </p>

      {list.status === "loading" && <p className="count" role="status">Loading…</p>}
      {list.status === "error" && (
        <div className="error" role="alert" data-testid="list-error">
          <p className="empty-title">Could not retrieve the demo question list</p>
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
        <label htmlFor="proposal-prompt"><strong>Change request (plain language)</strong></label>
        <textarea
          id="proposal-prompt"
          rows={3}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Example: Add a question that checks the API key revocation history"
        />
        <p className="hint">Example request to deactivate a question: Deactivate question A13</p>
        <div>
          <button type="submit" className="btn" disabled={busy || prompt.trim() === ""}>Create proposal</button>
        </div>
      </form>

      {error !== "" && <p className="error-inline" role="alert" data-testid="proposal-error">{error}</p>}
      {notice !== "" && <p className="notice-ok" role="status" data-testid="proposal-notice">{notice}</p>}

      {proposal !== null && (
        <section className="proposal" aria-labelledby="diff-title" data-testid="proposal">
          <h3 id="diff-title">Proposed change: {proposal.kind === "retire" ? `deactivate question ${proposal.retiredId}` : "add question"} (not applied yet)</h3>
          <SourceBadge source={proposal.source} />
          <div className="diff">
            <QuestionTable title="Before (current)" questions={proposal.before} />
            {proposal.kind === "retire" ? (
              <QuestionTable title="After (if applied)" questions={proposal.after} retiredId={proposal.retiredId} />
            ) : (
              <QuestionTable title="After (if applied)" questions={proposal.after} addedId={proposal.addedId} />
            )}
          </div>
          {proposal.kind === "retire" && <RetirementDetail proposal={proposal} />}
          <p className="confirm-q" id="confirm-q"><strong>Apply this change?</strong></p>
          <div className="confirm-actions" role="group" aria-labelledby="confirm-q">
            <button type="button" className="btn" disabled={busy} onClick={() => void decide("yes")}>Yes</button>
            <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => void decide("no")}>No</button>
          </div>
        </section>
      )}

      {list.status === "ok" && (
        <div className="current-list">
          <QuestionTable title="Current demo question list" questions={list.data.questions} />
        </div>
      )}
      {aiPrompt !== null && (
        <button type="button" className="btn ghost" disabled={busy} onClick={() => void create(true, aiPrompt)}>
          Re-propose with AI
        </button>
      )}
      {list.status === "ok" && list.data.templateReuse && (
        <TemplatePanel target="question" refreshKey={templateVersion} />
      )}
    </section>
  );
}
