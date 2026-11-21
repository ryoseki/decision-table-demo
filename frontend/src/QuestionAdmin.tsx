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
      <div className="pane-head"><h3>{title}</h3><span>{questions.length}問</span></div>
      <table className="case-table question-table">
        <thead><tr><th scope="col">ID</th><th scope="col">設問</th></tr></thead>
        <tbody>
          {questions.map((q) => {
            const retiring = q.id === retiredId;
            const rowClass = q.id === addedId ? "row-added" : retiring || q.retired === true ? "row-retired" : undefined;
            return (
              <tr key={q.id} className={rowClass} data-testid={`${title}-${q.id}`}>
                <th scope="row" className="case-id">{q.id}</th>
                <td>
                  {q.text}
                  {q.id === addedId && <span className="badge added-label">追加</span>}
                  {retiring && <span className="badge retired-label">廃止</span>}
                  {!retiring && q.retired === true && <span className="badge retired-label">廃止済み</span>}
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
      <h5>{title}（{cases.length}件）</h5>
      <p className="hint">{note}</p>
      {cases.length > 0 && (
        <ul>
          {cases.map((c) => (
            <li key={c.caseId} data-testid={`impact-${c.caseId}`}>
              <a href={detailHref(c.caseId)} title={c.caseId}>{c.caseId}</a> {c.summary}
              <span className="impact-detail"> 保存済みの回答: {answerLabel(c.answer)}</span>
              {c.result !== undefined && <span className="impact-detail"> 登録時の結果: {c.result}</span>}
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
      <section aria-label="参照しているルール">
        <h4>参照しているルール</h4>
        {references.length === 0 ? (
          <p>現行のルールに、設問{proposal.retiredId}を参照するものはありません。</p>
        ) : (
          <>
            <table className="case-table">
              <thead><tr><th scope="col">ルール</th><th scope="col">版</th><th scope="col">結果</th></tr></thead>
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
              参照するルールが{references.length}件あります。Yesを選んでも、設問{proposal.retiredId}は廃止されません（ルールは変更しません）。
            </p>
          </>
        )}
      </section>
      <section aria-label="既存案件への影響">
        <h4>既存案件への影響</h4>
        <CaseImpactList
          title="判定未登録の案件"
          cases={impact.unjudged}
          note="最新のルールで照合します。廃止した設問は、新たな回答の対象から外れます。保存済みの設問文と回答は、審査画面で確認できます。"
        />
        <CaseImpactList
          title="判定登録済みの案件"
          cases={impact.judged}
          note="登録時の結果と、保存済みの設問文・回答を表示します。廃止した設問には、新たに回答できません。"
        />
      </section>
      <p className="note" data-testid="no-cascade">
        この操作で、判定ルールと案件の保存データは変更しません（連鎖して変えません）。
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
            ? `デモの設問一覧に反映しました（設問${proposal.retiredId}を廃止。保存済みの設問文と回答は残ります）`
            : "デモの設問一覧に反映しました"
          : `この${retiring ? "廃止案" : "追加案"}は反映していません。下の一覧は、サーバーの最新の状態です。`,
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
        <h2 id="questions-title" tabIndex={-1}>設問管理（デモ）</h2>
        <p className="lead">自然文で、設問の追加案を作り、変更前と変更後の差分を確認して、反映するかを決めます。</p>
      </div>

      <p className="target-badge" data-testid="target">操作の対象: 設問</p>

      <p className="note note-top" data-testid="mock-note">
        <AiNote source={list.status === "ok" ? list.data.source : null} failed={list.status === "error"} />
        {list.status === "ok" && (list.data.persistence === "database" ? (
          <> 承認した設問は共有データベースに保存され、他の来訪者にも見えます。</>
        ) : (
          <> 反映先は、サーバーのメモリ上のデモの一覧だけです。再読み込みしても残りますが、バックエンドを再起動すると初期状態に戻ります（永続保存ではありません）。
        案件の設問・回答、データベースは変わりません。</>
        ))}
        設問を廃止すると、案件の画面では、その設問が新たな回答の対象から外れます（保存済みの設問文と回答は残ります）。
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
        <p className="hint">廃止の依頼の例: 設問A13を廃止して</p>
        <div>
          <button type="submit" className="btn" disabled={busy || prompt.trim() === ""}>追加案を作る</button>
        </div>
      </form>

      {error !== "" && <p className="error-inline" role="alert" data-testid="proposal-error">{error}</p>}
      {notice !== "" && <p className="notice-ok" role="status" data-testid="proposal-notice">{notice}</p>}

      {proposal !== null && (
        <section className="proposal" aria-labelledby="diff-title" data-testid="proposal">
          <h3 id="diff-title">{proposal.kind === "retire" ? "廃止案" : "追加案"}の確認（まだ反映していません）</h3>
          <SourceBadge source={proposal.source} />
          <div className="diff">
            <QuestionTable title="Before（現行）" questions={proposal.before} />
            {proposal.kind === "retire" ? (
              <QuestionTable title="After（反映した場合）" questions={proposal.after} retiredId={proposal.retiredId} />
            ) : (
              <QuestionTable title="After（反映した場合）" questions={proposal.after} addedId={proposal.addedId} />
            )}
          </div>
          {proposal.kind === "retire" && <RetirementDetail proposal={proposal} />}
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
      {aiPrompt !== null && (
        <button type="button" className="btn ghost" disabled={busy} onClick={() => void create(true, aiPrompt)}>
          AIで再提案
        </button>
      )}
      {list.status === "ok" && list.data.templateReuse && (
        <TemplatePanel target="question" refreshKey={templateVersion} />
      )}
    </section>
  );
}
