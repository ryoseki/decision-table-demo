// ts-react-dangerously-set-inner-html の検査用の架空コード。実行しない（Semgrepが読むだけ）。
// `// ruleid: <ルールID>` は、次の行で検出されること、`// ok: <ルールID>` は、次の行で検出されないことを示す。
export function Violations({ note }: { note: string }) {
  return (
    <div>
      {/* ruleid: ts-react-dangerously-set-inner-html */}
      <p dangerouslySetInnerHTML={{ __html: note }} />
      {/* ruleid: ts-react-dangerously-set-inner-html */}
      <p dangerouslySetInnerHTML={{ __html: `<b>${note}</b>` }} />
    </div>
  );
}

export function Controls({ note }: { note: string }) {
  return (
    <div>
      {/* ok: ts-react-dangerously-set-inner-html */}
      <p>{note}</p>
      {/* ok: ts-react-dangerously-set-inner-html */}
      <p dangerouslySetInnerHTML={{ __html: "<b>固定の文言</b>" }} />
    </div>
  );
}
