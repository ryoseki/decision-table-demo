// 実行: npm test（Vitest + jsdom）。個別審査の画面案（保存なし）を描画して、
// E2E（e2e/tests/review.spec.ts）と同じ挙動を、ブラウザなしで確認する。
// モバイル幅の横はみ出しは、実際のレイアウト計算が要るため、E2Eだけで確認する。
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import CaseDetail from "./CaseDetail";

// globalsを使わないため、描画の後始末は自分で登録する。
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const answer = (q: string, v: "Yes" | "No") =>
  fireEvent.click(within(screen.getByTestId(`q-${q}`)).getByRole("button", { name: v }));
const display = (r: string) => screen.getByTestId(`rule-${r}`).getAttribute("data-display");
const isOpen = (r: string) => (screen.getByTestId(`rule-${r}`) as HTMLDetailsElement).open;
const matched = (r: string, q: string) => screen.getByTestId(`cond-${r}-${q}`).classList.contains("matched");
const rules = (f: (r: string) => unknown) => ["R01", "R02", "R03"].map(f);

// 通信が起きていないことを確認できるよう、描画の前に fetch をスタブする（開いた時点の通信も検出する）。
let fetchSpy: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchSpy = vi.fn();
  vi.stubGlobal("fetch", fetchSpy);
  render(<CaseDetail caseId="CASE-B" />);
});

test("開いた直後は保存しない旨の注意を表示する", () => {
  expect(screen.getByTestId("preview-note").textContent).toContain("保存はまだ行いません");
});

test("例1: A01=Yes・A03=Yes でR01・R02は通常、R03はグレー。一致条件が緑", () => {
  answer("A01", "Yes");
  answer("A03", "Yes");
  expect(rules(display)).toEqual(["normal", "normal", "gray"]);
  expect(rules(isOpen)).toEqual([true, true, false]);
  expect(matched("R01", "A01")).toBe(true);
  expect(matched("R01", "A03")).toBe(true);
  expect(matched("R01", "A02")).toBe(false); // 未回答は緑にしない
  expect(screen.getByTestId("rule-count").textContent).toBe("照合 2／3件");
  // グレーの行には、食い違いを伝える読み上げ用の文言がある。通常の行にはない。
  expect(screen.getByTestId("rule-R03").textContent).toContain("入力済みの回答と異なる条件があります");
  expect(screen.getByTestId("rule-R01").textContent).not.toContain("入力済みの回答と異なる条件があります");
});

test("例2: A01をNoに訂正するとR01・R02はグレー、R03は通常で開く", () => {
  answer("A01", "Yes");
  answer("A03", "Yes");
  answer("A01", "No");
  expect(rules(display)).toEqual(["gray", "gray", "normal"]);
  expect(rules(isOpen)).toEqual([false, false, true]);
});

test("手動の開閉は回答が変わるまで維持される", () => {
  answer("A01", "Yes");
  const r03 = screen.getByTestId("rule-R03") as HTMLDetailsElement;
  // ブラウザの summary クリックと同じく、open を変えて toggle イベントを出す。
  r03.open = true;
  fireEvent(r03, new Event("toggle"));
  expect(isOpen("R03")).toBe(true);
  answer("A01", "Yes"); // 値が変わらない操作では開閉を更新しない
  expect(isOpen("R03")).toBe(true);
  answer("A03", "Yes"); // 値が変わると規則どおりに更新する（R03はグレーなので閉じる）
  expect(isOpen("R03")).toBe(false);
});

test("例3: 全行グレーでも結果「問題なし」を選んで［判定する］で画面例を表示できる", () => {
  answer("A01", "Yes");
  answer("A03", "No");
  expect(rules(display)).toEqual(["gray", "gray", "gray"]);
  expect(screen.getByTestId("rule-count").textContent).toBe("照合 0／3件");
  for (const r of ["アカウント削除", "アカウント一時凍結", "注意", "問題なし"]) {
    expect((screen.getByRole("radio", { name: r }) as HTMLInputElement).disabled).toBe(false);
  }
  fireEvent.click(screen.getByRole("radio", { name: "問題なし" }));
  fireEvent.click(screen.getByRole("button", { name: "判定する" }));
  expect(screen.getByTestId("registered").textContent).toBe("登録結果（画面例） 問題なし");
});

test("未回答のまま結果を選んで判定できる。保存・通信・ストレージは使わない", () => {
  fireEvent.click(screen.getByRole("radio", { name: "アカウント削除" }));
  fireEvent.click(screen.getByRole("button", { name: "判定する" }));
  expect(screen.getByTestId("registered").textContent).toBe("登録結果（画面例） アカウント削除");
  expect(fetchSpy).not.toHaveBeenCalled();
  expect([localStorage.length, sessionStorage.length]).toEqual([0, 0]);
});

test("開き直すと初期状態に戻る（登録結果は残らない）", () => {
  fireEvent.click(screen.getByRole("radio", { name: "アカウント削除" }));
  fireEvent.click(screen.getByRole("button", { name: "判定する" }));
  expect(screen.queryByTestId("registered")).not.toBeNull();
  cleanup();
  render(<CaseDetail caseId="CASE-B" />);
  expect(screen.queryByTestId("registered")).toBeNull();
});

test("確認待ちは食い違いにせず、Noで解消できる", () => {
  cleanup();
  render(<CaseDetail caseId="CASE-D" />);
  const q = screen.getByTestId("q-A09");
  const yes = within(q).getByRole("button", { name: "Yes" });
  const no = within(q).getByRole("button", { name: "No" });
  expect(q.textContent).toContain("確認待ち");
  expect([yes.getAttribute("aria-pressed"), no.getAttribute("aria-pressed")]).toEqual(["false", "false"]);
  fireEvent.click(no);
  expect(q.textContent).not.toContain("確認待ち");
  expect(no.getAttribute("aria-pressed")).toBe("true");
});

test("存在しない案件IDではエラーを表示し、一覧へ戻る導線を出す", () => {
  cleanup();
  render(<CaseDetail caseId="CASE-NONE" />);
  expect(screen.getByTestId("case-not-found").textContent).toContain("CASE-NONE");
  expect(screen.getAllByRole("link", { name: /一覧へ戻る/ }).length).toBeGreaterThan(0);
});

test("確認待ちの設問には、理由と、Yes・Noに数えない旨を表示する", () => {
  cleanup();
  render(<CaseDetail caseId="CASE-D" />);
  const q = screen.getByTestId("q-A09");
  expect(q.textContent).toContain("APIキー発行履歴の自動回答を取得できませんでした");
  expect(q.textContent).toContain("Yes・Noのどちらにも数えません");
});

test("「自動」の印は、人が回答を選ぶと消える", () => {
  cleanup();
  render(<CaseDetail caseId="CASE-D" />);
  const q = screen.getByTestId("q-A01");
  expect(q.textContent).toContain("自動");
  fireEvent.click(within(q).getByRole("button", { name: "No" }));
  expect(q.textContent).not.toContain("自動");
});

test("登録済みの案件は、登録済みの結果を表示する", () => {
  cleanup();
  render(<CaseDetail caseId="CASE-E" />);
  expect(screen.getByText("登録済みの結果: 問題なし")).toBeTruthy();
});

test("未回答のとき、状態バッジは未審査を表示する", () => {
  expect(screen.getByText("未審査")).toBeTruthy();
});
