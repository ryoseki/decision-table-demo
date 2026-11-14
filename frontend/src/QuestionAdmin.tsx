import { useState } from "react";
import { ApiError, describeError } from "./api";
import {
  createQuestionProposal,
  decideQuestionProposal,
  type DemoQuestion,
  type DemoQuestionList,
  type QuestionProposal,
} from "./questionsApi";
import { useDemoQuestions } from "./useApi";

type ListState = { status: "loading" } | { status: "ok"; data: DemoQuestionList } | { status: "error"; message: string };

function QuestionTable({ title, questions, addedId }: { title: string; questions: DemoQuestion[]; addedId?: string }) {
  return (
    <section className="pane" aria-label={title}>
      <div className="pane-head"><h3>{title}</h3><span>{questions.length}問</span></div>
      <table className="case-table question-table">
        <thead><tr><th scope="col">ID</th><th scope="col">設問</th></tr></thead>
        <tbody>
          {questions.map((q) => (
            <tr key={q.id} className={q.id === addedId ? "row-added" : undefined} data-testid={`${title}-${q.id}`}>
              <th scope="row" className="case-id">{q.id}</th>
              <td>
                {q.text}
                {q.id === addedId && <span className="badge added-label">追加</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

// 設問管理（デモ）。AIの応答はモックで、反映先はサーバーのメモリ上のデモ一覧（DBではない）。
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

  const create = async () => {
    setBusy(true);
    setNotice("");
    setError("");
    setProposal(null);
    try {
      setProposal(await createQuestionProposal(prompt));
    } catch (e) {
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
      const result = await decideQuestionProposal(proposal.proposalId, decision);
      setLatest({ revision: result.revision, questions: result.questions });
      setProposal(null);
      setNotice(result.applied ? "デモの設問一覧に反映しました" : "この追加案は反映していません。下の一覧は、サーバーの最新の状態です。");
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
        <h2 id="questions-title" tabIndex={-1}>設問管理（デモ）</h2>
        <p className="lead">自然文で、設問の追加案を作り、変更前と変更後の差分を確認して、反映するかを決めます。</p>
      </div>

      <p className="note note-top" data-testid="mock-note">
        <strong>モックです。</strong>AIの応答は、固定の規則で作った架空のもので、実際のAIには接続していません。
        反映先は、サーバーのメモリ上のデモの一覧だけです。再読み込みしても残りますが、バックエンドを再起動すると初期状態に戻ります（永続保存ではありません）。
        案件の設問・回答、データベースは変わりません。
      </p>

      {list.status === "loading" && <p className="count" role="status">読み込み中…</p>}
      {list.status === "error" && (
        <div className="error" role="alert" data-testid="list-error">
          <p className="empty-title">デモの設問一覧を取得できませんでした</p>
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
        <label htmlFor="proposal-prompt"><strong>変更の依頼（自然文）</strong></label>
        <textarea
          id="proposal-prompt"
          rows={3}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="例: APIキーの失効履歴を確認する設問を追加して"
        />
        <div>
          <button type="submit" className="btn" disabled={busy || prompt.trim() === ""}>追加案を作る</button>
        </div>
      </form>

      {error !== "" && <p className="error-inline" role="alert" data-testid="proposal-error">{error}</p>}
      {notice !== "" && <p className="notice-ok" role="status" data-testid="proposal-notice">{notice}</p>}

      {proposal !== null && (
        <section className="proposal" aria-labelledby="diff-title" data-testid="proposal">
          <h3 id="diff-title">追加案の確認（まだ反映していません）</h3>
          <div className="diff">
            <QuestionTable title="Before（現行）" questions={proposal.before} />
            <QuestionTable title="After（反映した場合）" questions={proposal.after} addedId={proposal.addedId} />
          </div>
          <p className="confirm-q" id="confirm-q"><strong>反映しますか？</strong></p>
          <div className="confirm-actions" role="group" aria-labelledby="confirm-q">
            <button type="button" className="btn" disabled={busy} onClick={() => void decide("yes")}>Yes</button>
            <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => void decide("no")}>No</button>
          </div>
        </section>
      )}

      {list.status === "ok" && (
        <div className="current-list">
          <QuestionTable title="現行のデモの設問一覧" questions={list.data.questions} />
        </div>
      )}
    </section>
  );
}
