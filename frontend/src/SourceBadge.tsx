import type { GenerationSource } from "./generationSource";

/** 差分（提案）の確認画面に出す、生成元の表示。モックと実接続を、文字で区別する（色だけに頼らない）。 */
export function SourceBadge({ source }: { source: GenerationSource }) {
  if (source.kind === "template") {
    return <p className="source-badge" data-testid="source-badge" data-source="template">生成元: 定型文（Bedrock呼び出しなし）</p>;
  }
  return source.kind === "bedrock" ? (
    <p className="source-badge source-bedrock" data-testid="source-badge" data-source="bedrock">
      <strong>生成元: Amazon Bedrock（実接続）</strong>
      <span className="source-detail"> モデル: {source.modelId} / リージョン: {source.region}</span>
    </p>
  ) : (
    <p className="source-badge source-mock" data-testid="source-badge" data-source="mock">
      <strong>生成元: モック</strong>
      <span className="source-detail"> 固定の規則で作った架空の応答です（実際のAIではありません）</span>
    </p>
  );
}

/**
 * 画面の説明の、AIの部分。生成元がまだ分からないとき（一覧の読み込み前・失敗）は null。
 * failed は、一覧の取得に失敗したとき（読み込み中のまま、と誤解させない）。
 */
export function AiNote({ source, failed = false }: { source: GenerationSource | null; failed?: boolean }) {
  if (source === null) {
    return failed ? <>AIの生成元を確認できませんでした（一覧を取得できていません）。</> : <>AIの生成元を確認しています。</>;
  }
  if (source.kind === "bedrock") {
    return (
      <>
        <strong>実接続です（Amazon Bedrock）。</strong>AIの応答は、Amazon Bedrock（モデル: {source.modelId}、リージョン: {source.region}）が生成します。
        依頼のたびに、利用料金が発生します。AIの出力はアプリが検証し、Yesを選ぶまで反映しません。
      </>
    );
  }
  return (
    <>
      <strong>モックです。</strong>AIの応答は、固定の規則で作った架空のもので、実際のAIには接続していません。
    </>
  );
}
