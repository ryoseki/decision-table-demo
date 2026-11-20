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
            {isAdded && "（追加）"}{" "}
          </span>
        );
      })}
    </>
  );
}

/** 提案の種類ごとの呼び名。 */
const KIND_NAME = { add: "追加案", remove: "削除案", update: "更新案" } as const;

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
            <th scope="col">ルール</th><th scope="col">版</th><th scope="col">結果</th>
            {grid ? columns.map((q) => <th key={q} scope="col">{q}</th>) : <th scope="col">条件（設問＝回答）</th>}
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
                  {adding && <span className="badge added-label">追加</span>}
                  {removing && <span className="badge retired-label">削除</span>}
                  {updating && <span className="badge updated-label">更新</span>}
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
      <div className="pane-head"><h3>{title}</h3><span>{rules.length}件</span></div>
      {grid ? (
        <div className="rule-grid-scroll" role="group" tabIndex={0} aria-label={`${title}の表（横にスクロールできます）`}>{table}</div>
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
      const applied = { add: "を追加", remove: "を削除", update: "の条件を更新" }[proposal.kind];
      setNotice(
        result.applied
          ? `デモの判定ルール一覧に反映しました（ルール${proposal.targetId}${applied}）`
          : `この${KIND_NAME[proposal.kind]}は反映していません。下の一覧は、サーバーの最新の状態です。`,
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
        <h2 id="rules-title" tabIndex={-1}>判定ルール管理（デモ）</h2>
        <p className="lead">自然文で、判定ルールの追加案・削除案・更新案（既存ルールへの条件の追加）を作り、変更前と変更後の差分を確認して、反映するかを決めます。</p>
      </div>
      <p className="target-badge" data-testid="target">操作の対象: 判定ルール</p>

      <p className="note note-top" data-testid="mock-note">
        <AiNote source={list.status === "ok" ? list.data.source : null} failed={list.status === "error"} />
        {list.status === "ok" && (list.data.persistence === "database" ? (
          <> 承認したルールは全員に共有され、判定未登録の案件を開いたときの照合に使われます。</>
        ) : (
          <> 反映先は、サーバーのメモリ上のデモのルール一覧だけです。再読み込みしても残りますが、バックエンドを再起動すると初期状態に戻ります（永続保存ではありません）。
        審査画面で照合に使うルール、案件、データベースは変わりません。</>
        ))}
        ルールが参照する設問・結果は、アプリが検証します（存在しない設問、定義にない結果、重複するルールは拒否します）。
      </p>

      {list.status === "loading" && <p className="count" role="status">読み込み中…</p>}
      {list.status === "error" && (
        <div className="error" role="alert" data-testid="list-error">
          <p className="empty-title">デモの判定ルール一覧を取得できませんでした</p>
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
        <label htmlFor="rule-prompt"><strong>変更の依頼（自然文）</strong></label>
        <textarea
          id="rule-prompt"
          rows={3}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="例: 設問A09がYes、設問A10がNoのとき注意とするルールを追加して"
        />
        <p className="hint">削除の依頼の例: ルールR03を削除して</p>
        <p className="hint">既存のルールへの条件の追加（更新）の依頼の例: R04にA09がYesを追加して</p>
        <div>
          <button type="submit" className="btn" disabled={busy || prompt.trim() === ""}>案を作る</button>
        </div>
      </form>

      {error !== "" && <p className="error-inline" role="alert" data-testid="proposal-error">{error}</p>}
      {notice !== "" && <p className="notice-ok" role="status" data-testid="proposal-notice">{notice}</p>}

      {proposal !== null && (
        <section className="proposal" aria-labelledby="rule-diff-title" data-testid="proposal">
          <h3 id="rule-diff-title">判定ルールの{KIND_NAME[proposal.kind]}の確認（まだ反映していません）</h3>
          <SourceBadge source={proposal.source} />
          {proposal.kind === "update" && (
            <p className="update-summary" data-testid="update-summary">
              更新の対象: ルール{proposal.targetId}（ID・既存の条件・結果は変わらず、条件を追加します）。追加する条件:{" "}
              {proposal.addedConditions.map((c) => `${c.questionId}=${ANSWER_LABEL[c.answer]}`).join("、")}
            </p>
          )}
          <div className="diff">
            <RuleTable
              title="Before（現行）"
              rules={proposal.before}
              removedId={proposal.kind === "remove" ? proposal.targetId : undefined}
              updatedId={proposal.kind === "update" ? proposal.targetId : undefined}
            />
            <RuleTable
              title="After（反映した場合）"
              rules={proposal.after}
              addedId={proposal.kind === "add" ? proposal.targetId : undefined}
              updatedId={proposal.kind === "update" ? proposal.targetId : undefined}
              addedQuestions={proposal.addedConditions.map((c) => c.questionId)}
            />
          </div>
          <p className="confirm-q" id="rule-confirm-q"><strong>反映しますか？</strong></p>
          <div className="confirm-actions" role="group" aria-labelledby="rule-confirm-q">
            <button type="button" className="btn" disabled={busy} onClick={() => void decide("yes")}>Yes</button>
            <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => void decide("no")}>No</button>
          </div>
        </section>
      )}

      {list.status === "ok" && (
        <div className="current-list">
          <RuleTable title="現行のデモの判定ルール一覧" rules={list.data.rules} grid />
        </div>
      )}
      {aiPrompt !== null && (
        <button type="button" className="btn ghost" disabled={busy} onClick={() => void create(true, aiPrompt)}>
          AIで再提案
        </button>
      )}
      {list.status === "ok" && list.data.templateReuse && (
        <TemplatePanel target="rule" refreshKey={templateVersion} />
      )}
    </section>
  );
}
