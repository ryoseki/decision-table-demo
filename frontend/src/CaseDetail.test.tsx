// 実行: npm test（Vitest + jsdom）。個別審査の画面を描画して、見える内容と操作を確認する。
// Review（画面の本体）は、固定の架空案件と、保存の関数（モック）で確認する。CaseDetail（取得と保存の
// つなぎ）は、fetch のスタブで確認する。サーバーには接続しない。
// モバイル幅の横はみ出しは、実際のレイアウト計算が要るため、E2Eだけで確認する。
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ApiError } from "./api";
import CaseDetail, { Review } from "./CaseDetail";
import { cases } from "./data/cases";
import { apiCase, stubFetch } from "./testing";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const fixture = (id: string) => cases.find((c) => c.caseId === id)!;
const answer = (q: string, v: "Yes" | "No") =>
  fireEvent.click(within(screen.getByTestId(`q-${q}`)).getByRole("button", { name: v }));
const display = (r: string) => screen.getByTestId(`rule-${r}`).getAttribute("data-display");
const isOpen = (r: string) => (screen.getByTestId(`rule-${r}`) as HTMLDetailsElement).open;
const matched = (r: string, q: string) => screen.getByTestId(`cond-${r}-${q}`).classList.contains("matched");
const rules = (f: (r: string) => unknown) => ["R01", "R02", "R03"].map(f);
const choose = (name: string) => fireEvent.click(screen.getByRole("radio", { name }));
const submit = () => fireEvent.click(screen.getByRole("button", { name: "判定する" }));

// ---- Review（画面の本体） -------------------------------------------------------

let save: ReturnType<typeof vi.fn>;
beforeEach(() => {
  save = vi.fn().mockResolvedValue(undefined);
});
const renderReview = (id: string) => render(<Review c={fixture(id)} save={save} />);

test("開いた直後は、結果が保存される旨と、回答は保存されない旨を表示する", () => {
  renderReview("CASE-B");
  const note = screen.getByTestId("save-note").textContent;
  expect(note).toContain("［判定する］で保存されます");
  expect(note).toContain("回答の変更は、まだ保存されません");
});

test("例1: A01=Yes・A03=Yes でR01・R02は通常、R03はグレー。一致条件が緑", () => {
  renderReview("CASE-B");
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
  renderReview("CASE-B");
  answer("A01", "Yes");
  answer("A03", "Yes");
  answer("A01", "No");
  expect(rules(display)).toEqual(["gray", "gray", "normal"]);
  expect(rules(isOpen)).toEqual([false, false, true]);
});

test("手動の開閉は回答が変わるまで維持される", () => {
  renderReview("CASE-B");
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

test("例3: 全行グレーでも、4つの結果をすべて選べる", () => {
  renderReview("CASE-B");
  answer("A01", "Yes");
  answer("A03", "No");
  expect(rules(display)).toEqual(["gray", "gray", "gray"]);
  expect(screen.getByTestId("rule-count").textContent).toBe("照合 0／3件");
  for (const r of ["アカウント削除", "アカウント一時凍結", "注意", "問題なし"]) {
    expect((screen.getByRole("radio", { name: r }) as HTMLInputElement).disabled).toBe(false);
  }
});

test("未回答のまま、結果を選んで判定できる。選んだ結果を、保存の関数に渡す", async () => {
  renderReview("CASE-B");
  choose("アカウント削除");
  submit();
  expect((await screen.findByTestId("registered")).textContent).toBe("保存しました。登録結果: アカウント削除");
  expect(save).toHaveBeenCalledTimes(1);
  expect(save).toHaveBeenCalledWith("アカウント削除");
  expect(screen.getByText("登録済みの結果: アカウント削除")).toBeTruthy(); // ヘッダーも更新する
  expect(screen.getByText("登録済み")).toBeTruthy(); // 状態バッジも登録済みになる
});

test("結果を選んでいないときは、保存しない", () => {
  renderReview("CASE-B");
  submit();
  expect(save).not.toHaveBeenCalled();
});

test("保存に失敗したら、エラーを表示し、保存済みの表示は変えない。もう一度押せる", async () => {
  save.mockRejectedValueOnce(new ApiError(500, "サーバーがエラーを返しました（HTTP 500）"));
  renderReview("CASE-B");
  choose("注意");
  submit();
  const alert = await screen.findByTestId("save-error");
  expect(alert.textContent).toContain("保存できませんでした");
  expect(alert.textContent).toContain("HTTP 500");
  expect(screen.queryByTestId("registered")).toBeNull();
  expect(screen.queryByText(/登録済みの結果/)).toBeNull();
  expect((screen.getByRole("button", { name: "判定する" }) as HTMLButtonElement).disabled).toBe(false);
  submit(); // 2回目は成功する
  expect(await screen.findByTestId("registered")).toBeTruthy();
  expect(screen.queryByTestId("save-error")).toBeNull();
});

test("保存中は、ボタンを無効にして、二重に送信しない", async () => {
  let finish: () => void = () => {};
  save.mockReturnValueOnce(new Promise<void>((resolve) => (finish = resolve)));
  renderReview("CASE-B");
  choose("注意");
  submit();
  const button = await screen.findByRole("button", { name: "保存中…" });
  expect((button as HTMLButtonElement).disabled).toBe(true);
  fireEvent.submit(screen.getByRole("form", { name: "判定結果登録" })); // 無効でも、submit が来ても送らない
  expect(save).toHaveBeenCalledTimes(1);
  await act(async () => finish());
  expect(await screen.findByTestId("registered")).toBeTruthy();
});

test("登録済みの案件を開くと、保存済みの結果を表示し、その結果が選ばれている", () => {
  renderReview("CASE-E");
  expect(screen.getByText("登録済みの結果: 問題なし")).toBeTruthy();
  expect((screen.getByRole("radio", { name: "問題なし" }) as HTMLInputElement).checked).toBe(true);
  expect(screen.queryByTestId("registered")).toBeNull(); // 「保存しました」は、保存した直後だけ
});

test("確認待ちは食い違いにせず、Noで解消できる", () => {
  renderReview("CASE-D");
  const q = screen.getByTestId("q-A09");
  const yes = within(q).getByRole("button", { name: "Yes" });
  const no = within(q).getByRole("button", { name: "No" });
  expect(q.textContent).toContain("確認待ち");
  expect([yes.getAttribute("aria-pressed"), no.getAttribute("aria-pressed")]).toEqual(["false", "false"]);
  fireEvent.click(no);
  expect(q.textContent).not.toContain("確認待ち");
  expect(no.getAttribute("aria-pressed")).toBe("true");
});

test("確認待ちの設問には、理由と、Yes・Noに数えない旨を表示する", () => {
  renderReview("CASE-D");
  const q = screen.getByTestId("q-A09");
  expect(q.textContent).toContain("APIキー発行履歴の自動回答を取得できませんでした");
  expect(q.textContent).toContain("Yes・Noのどちらにも数えません");
});

test("「自動」の印は、人が回答を選ぶと消える", () => {
  renderReview("CASE-D");
  const q = screen.getByTestId("q-A01");
  expect(q.textContent).toContain("自動");
  fireEvent.click(within(q).getByRole("button", { name: "No" }));
  expect(q.textContent).not.toContain("自動");
});

test("未回答で結果がないとき、状態バッジは未審査を表示する", () => {
  renderReview("CASE-B");
  expect(screen.getByText("未審査")).toBeTruthy();
});

// ---- CaseDetail（取得と保存のつなぎ） -----------------------------------------------

test("取得中は読み込み中を表示し、取得できたら案件を表示する（A09 の回答も）", async () => {
  stubFetch({ "GET /api/cases/CASE-X": { body: apiCase() } });
  render(<CaseDetail caseId="CASE-X" />);
  expect(screen.getByTestId("case-loading")).toBeTruthy();
  expect(await screen.findByTestId("save-note")).toBeTruthy();
  expect(screen.queryByTestId("case-loading")).toBeNull();
  expect(screen.getByRole("heading", { name: /架空の概要/ })).toBeTruthy();
  const yes = within(screen.getByTestId("q-A09")).getByRole("button", { name: "Yes" });
  expect(yes.getAttribute("aria-pressed")).toBe("true"); // 受付で取得済みの値
  const a01 = within(screen.getByTestId("q-A01")).getByRole("button", { name: "Yes" });
  expect(a01.getAttribute("aria-pressed")).toBe("false"); // 未回答
});

test("存在しない案件（404）は、見つからない旨と、一覧へ戻る導線を表示する", async () => {
  stubFetch({ "GET /api/cases/CASE-NONE": { status: 404, body: { detail: "案件がありません" } } });
  render(<CaseDetail caseId="CASE-NONE" />);
  expect((await screen.findByTestId("case-not-found")).textContent).toContain("CASE-NONE");
  expect(screen.getAllByRole("link", { name: /一覧へ戻る/ }).length).toBeGreaterThan(0);
});

test("取得に失敗したら（500・通信エラー）、エラーを表示する。見つからない旨とは区別する", async () => {
  stubFetch({ "GET /api/cases/CASE-X": { status: 500 } });
  const { unmount } = render(<CaseDetail caseId="CASE-X" />);
  const alert = await screen.findByTestId("case-error");
  expect(alert.textContent).toContain("HTTP 500");
  expect(screen.queryByTestId("case-not-found")).toBeNull();
  unmount();
  stubFetch({ "GET /api/cases/CASE-X": new TypeError("fetch failed") });
  render(<CaseDetail caseId="CASE-X" />);
  expect((await screen.findByTestId("case-error")).textContent).toContain("接続できませんでした");
});

test("結果を選んで判定すると、PUT で結果コードを送り、保存済みの結果を表示する", async () => {
  const spy = stubFetch({
    "GET /api/cases/CASE-X": { body: apiCase() },
    "PUT /api/cases/CASE-X/judgment": { status: 204 },
  });
  render(<CaseDetail caseId="CASE-X" />);
  await screen.findByTestId("save-note");
  choose("注意");
  submit();
  expect((await screen.findByTestId("registered")).textContent).toBe("保存しました。登録結果: 注意");
  const put = spy.mock.calls.find(([, init]) => init?.method === "PUT")!;
  expect(String(put[0])).toBe("/api/cases/CASE-X/judgment");
  expect(JSON.parse((put[1] as RequestInit).body as string)).toEqual({ result_code: "caution" });
});

test("保存が拒否（422）されたら、エラーを表示する", async () => {
  stubFetch({
    "GET /api/cases/CASE-X": { body: apiCase() },
    "PUT /api/cases/CASE-X/judgment": { status: 422, body: { detail: "x" } },
  });
  render(<CaseDetail caseId="CASE-X" />);
  await screen.findByTestId("save-note");
  choose("注意");
  submit();
  expect((await screen.findByTestId("save-error")).textContent).toContain("HTTP 422");
});

test("保存済みの案件を開くと、保存済みの結果を表示する", async () => {
  stubFetch({ "GET /api/cases/CASE-X": { body: apiCase({ result: { result_code: "account_suspension" } }) } });
  render(<CaseDetail caseId="CASE-X" />);
  expect(await screen.findByText("登録済みの結果: アカウント一時凍結")).toBeTruthy();
});

test("詳細で、定義にない結果コードが返ったら、エラーを表示し、別の結果に見せない", async () => {
  stubFetch({ "GET /api/cases/CASE-X": { body: apiCase({ result: { result_code: "unknown" } }) } });
  render(<CaseDetail caseId="CASE-X" />);
  expect((await screen.findByTestId("case-error")).textContent).toContain("予期しないエラー");
  expect(screen.queryByText(/登録済みの結果/)).toBeNull();
  expect(screen.queryByTestId("save-note")).toBeNull();
});

test("取得の呼び出しは、案件IDのURLに1回だけ行う", async () => {
  const spy = stubFetch({ "GET /api/cases/CASE-X": { body: apiCase() } });
  render(<CaseDetail caseId="CASE-X" />);
  await screen.findByTestId("save-note");
  expect(spy).toHaveBeenCalledTimes(1);
  expect(String(spy.mock.calls[0][0])).toBe("/api/cases/CASE-X");
});

test("保存の通信エラー（PUT）は、接続できなかった旨を表示し、保存済みの表示を変えない", async () => {
  stubFetch({
    "GET /api/cases/CASE-X": { body: apiCase() },
    "PUT /api/cases/CASE-X/judgment": new TypeError("fetch failed"),
  });
  render(<CaseDetail caseId="CASE-X" />);
  await screen.findByTestId("save-note");
  choose("注意");
  submit();
  expect((await screen.findByTestId("save-error")).textContent).toContain("接続できませんでした");
  expect(screen.queryByTestId("registered")).toBeNull();
  expect(screen.queryByText(/登録済みの結果/)).toBeNull();
});

test("保存に失敗して、もう一度押すと成功する（実際の fetch の経路）", async () => {
  let puts = 0;
  stubFetch({
    "GET /api/cases/CASE-X": { body: apiCase() },
    "PUT /api/cases/CASE-X/judgment": () => (++puts === 1 ? { status: 500 } : { status: 204 }),
  });
  render(<CaseDetail caseId="CASE-X" />);
  await screen.findByTestId("save-note");
  choose("注意");
  submit();
  await screen.findByTestId("save-error");
  submit();
  expect(await screen.findByTestId("registered")).toBeTruthy();
  expect(screen.queryByTestId("save-error")).toBeNull();
  expect(puts).toBe(2);
});

test("保存したあと、別の結果を選んで判定し直すと、保存済みの結果が更新される", async () => {
  renderReview("CASE-B");
  choose("注意");
  submit();
  await screen.findByText("登録済みの結果: 注意");
  choose("問題なし");
  submit();
  await screen.findByText("登録済みの結果: 問題なし");
  expect(save).toHaveBeenNthCalledWith(1, "注意");
  expect(save).toHaveBeenNthCalledWith(2, "問題なし");
  expect(screen.queryByText("登録済みの結果: 注意")).toBeNull();
});

test("案件を切り替えたあとに、古い案件の応答が遅れて届いても、表示を上書きしない", async () => {
  const pending: Record<string, (r: Response) => void> = {};
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => new Promise<Response>((resolve) => (pending[url] = resolve))),
  );
  const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
  const { rerender } = render(<CaseDetail caseId="CASE-A" />);
  rerender(<CaseDetail caseId="CASE-B" />);
  await act(async () => pending["/api/cases/CASE-B"](json(apiCase({ case_id: "CASE-B", summary: "案件Bの概要" }))));
  expect(await screen.findByRole("heading", { name: /案件Bの概要/ })).toBeTruthy();
  await act(async () => pending["/api/cases/CASE-A"](json(apiCase({ case_id: "CASE-A", summary: "案件Aの概要" }))));
  expect(screen.getByRole("heading", { name: /案件Bの概要/ })).toBeTruthy(); // 古い応答で上書きしない
  expect(screen.queryByText(/案件Aの概要/)).toBeNull();
});
