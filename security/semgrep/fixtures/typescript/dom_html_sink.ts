// ts-dom-html-sink の検査用の架空コード。実行しない（Semgrepが読むだけ）。
export function violations(el: HTMLElement, note: string) {
  // ruleid: ts-dom-html-sink
  el.innerHTML = note;
  // ruleid: ts-dom-html-sink
  el.outerHTML = `<p>${note}</p>`;
  // ruleid: ts-dom-html-sink
  el.insertAdjacentHTML("beforeend", note);
  // ruleid: ts-dom-html-sink
  document.write(note);
  // ruleid: ts-dom-html-sink
  document.writeln(note);
}

export function controls(el: HTMLElement, note: string) {
  // ok: ts-dom-html-sink
  el.textContent = note;
  // ok: ts-dom-html-sink
  el.innerHTML = "<p>固定の文言</p>";
  // ok: ts-dom-html-sink
  el.insertAdjacentText("beforeend", note);
  // ok: ts-dom-html-sink
  el.outerHTML = "<p>固定の文言</p>";
}
