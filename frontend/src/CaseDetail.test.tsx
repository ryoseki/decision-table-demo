// 実行: npm test（Vitest + jsdom）。個別審査の画面を描画して、見える内容と操作を確認する。
// Review（画面の本体）は、固定の架空案件と、保存の関数（モック）で確認する。CaseDetail（取得と保存の
// つなぎ）は、fetch のスタブで確認する。サーバーには接続しない。
// モバイル幅の横はみ出しは、実際のレイアウト計算が要るため、E2Eだけで確認する。
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ApiError } from "./api";
import CaseDetail, { Review } from "./CaseDetail";
import { cases } from "./data/cases";
import { DEMO_RULES } from "./data/demoRules";
import { apiCase, apiRules, stubFetch } from "./testing";

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
const submit = () => fireEvent.click(screen.getByRole("button", { name: "Submit judgment" }));

// ---- Review（画面の本体） -------------------------------------------------------

let save: ReturnType<typeof vi.fn>;
beforeEach(() => {
  save = vi.fn().mockResolvedValue(undefined);
});
const renderReview = (id: string) => render(<Review c={fixture(id)} rules={DEMO_RULES} save={save} />);

test("開いた直後は、結果と変更した回答が［判定する］で保存される旨と、離れると保存されない旨を表示する", () => {
  renderReview("CASE-B");
  const note = screen.getByTestId("save-note").textContent;
  expect(note).toContain("changed answers are saved with [Submit judgment]");
  expect(note).toContain("leave without pressing [Submit judgment], your changes are not saved");
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
  expect(screen.getByTestId("rule-count").textContent).toBe("2 of 3 rules match");
  // グレーの行には、食い違いを伝える読み上げ用の文言がある。通常の行にはない。
  expect(screen.getByTestId("rule-R03").textContent).toContain("Some conditions differ from the answers entered");
  expect(screen.getByTestId("rule-R01").textContent).not.toContain("Some conditions differ from the answers entered");
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
  expect(screen.getByTestId("rule-count").textContent).toBe("0 of 3 rules match");
  for (const r of ["Account deletion", "Account suspension", "Caution", "No issue"]) {
    expect((screen.getByRole("radio", { name: r }) as HTMLInputElement).disabled).toBe(false);
  }
});

test("未回答のまま、結果を選んで判定できる。選んだ結果を、保存の関数に渡す", async () => {
  renderReview("CASE-B");
  choose("Account deletion");
  submit();
  expect((await screen.findByTestId("registered")).textContent).toBe("Saved. Registered result: Account deletion");
  expect(save).toHaveBeenCalledTimes(1);
  expect(save).toHaveBeenCalledWith("Account deletion", {}, null); // 回答を変えていないので、回答は送らない
  expect(screen.getByText("Registered result: Account deletion")).toBeTruthy(); // ヘッダーも更新する
  expect(screen.getByText("Registered")).toBeTruthy(); // 状態バッジも登録済みになる
});

test("結果を選んでいないときは、保存しない", () => {
  renderReview("CASE-B");
  submit();
  expect(save).not.toHaveBeenCalled();
});

test("保存に失敗したら、エラーを表示し、保存済みの表示は変えない。もう一度押せる", async () => {
  save.mockRejectedValueOnce(new ApiError(500, "The server returned an error (HTTP 500）"));
  renderReview("CASE-B");
  choose("Caution");
  submit();
  const alert = await screen.findByTestId("save-error");
  expect(alert.textContent).toContain("Could not save");
  expect(alert.textContent).toContain("HTTP 500");
  expect(screen.queryByTestId("registered")).toBeNull();
  expect(screen.queryByText(/Registered result/)).toBeNull();
  expect((screen.getByRole("button", { name: "Submit judgment" }) as HTMLButtonElement).disabled).toBe(false);
  submit(); // 2回目は成功する
  expect(await screen.findByTestId("registered")).toBeTruthy();
  expect(screen.queryByTestId("save-error")).toBeNull();
});

test("保存が503（混み合っている）なら、その旨を表示し、選んだ結果を保ち、もう一度押せる", async () => {
  save.mockRejectedValueOnce(
    new ApiError(503, "The server is busy. Your input is kept. Please try again in a while (HTTP 503)"),
  );
  renderReview("CASE-B");
  choose("Caution");
  submit();
  const alert = await screen.findByTestId("save-error");
  expect(alert.textContent).toContain("busy");
  expect(alert.textContent).toContain("Your input is kept");
  expect(screen.queryByTestId("registered")).toBeNull();
  expect((screen.getByRole("radio", { name: /Caution/ }) as HTMLInputElement).checked).toBe(true); // 選んだ結果は残る
  submit();
  expect(await screen.findByTestId("registered")).toBeTruthy();
  expect(save).toHaveBeenCalledTimes(2);
});

test("保存中は、ボタンを無効にして、二重に送信しない", async () => {
  let finish: () => void = () => {};
  save.mockReturnValueOnce(new Promise<void>((resolve) => (finish = resolve)));
  renderReview("CASE-B");
  choose("Caution");
  submit();
  const button = await screen.findByRole("button", { name: "Saving…" });
  expect((button as HTMLButtonElement).disabled).toBe(true);
  fireEvent.submit(screen.getByRole("form", { name: "Submit judgment" })); // 無効でも、submit が来ても送らない
  expect(save).toHaveBeenCalledTimes(1);
  await act(async () => finish());
  expect(await screen.findByTestId("registered")).toBeTruthy();
});

test("登録済みの案件を開くと、保存済みの結果を表示し、その結果が選ばれている", () => {
  renderReview("CASE-E");
  expect(screen.getByText("Registered result: No issue")).toBeTruthy();
  expect((screen.getByRole("radio", { name: "No issue" }) as HTMLInputElement).checked).toBe(true);
  expect(screen.queryByTestId("registered")).toBeNull(); // 「保存しました」は、保存した直後だけ
});

// ---- 判定登録済みの案件は閲覧のみ（2026-11-15の合意。旧「判定し直せる」を置換） ------------------------

const answerButtons = () => screen.getAllByRole("button", { name: /^(Yes|No)$/ }) as HTMLButtonElement[];
const radios = () => screen.getAllByRole("radio") as HTMLInputElement[];

test("登録済みの案件を開くと閲覧のみ: 保存済みの回答と結果が見え、回答・結果・採用の操作と［判定する］がない", () => {
  renderReview("CASE-E"); // A01=Yes、A03=No、結果は問題なし
  expect(screen.getByTestId("save-note").textContent).toContain("judgment has already been registered");
  expect(screen.getByTestId("save-note").textContent).toContain("read-only");
  expect(screen.getByTestId("readonly-note").textContent).toContain("read-only");
  expect(screen.queryByRole("button", { name: "Submit judgment" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Saving…" })).toBeNull();
  const buttons = answerButtons();
  expect(buttons).toHaveLength(24); // 12問×Yes・No。表示だけで、すべて押せない
  expect(buttons.every((b) => b.disabled)).toBe(true);
  expect(radios().every((r) => r.disabled)).toBe(true);
  // 保存済みの回答がそのまま見える
  expect(within(screen.getByTestId("q-A01")).getByRole("button", { name: "Yes" }).getAttribute("aria-pressed")).toBe("true");
  expect(within(screen.getByTestId("q-A03")).getByRole("button", { name: "No" }).getAttribute("aria-pressed")).toBe("true");
  expect(screen.queryByRole("button", { name: /Adopt this rule|Remove adoption/ })).toBeNull();
});

test("登録済みの案件では、回答のボタンを押しても、表示も保存も変わらない（押せない状態を迂回しても）", () => {
  renderReview("CASE-E");
  const a01No = within(screen.getByTestId("q-A01")).getByRole("button", { name: "No" });
  fireEvent.click(a01No); // 無効なボタン。クリックのイベントが来ても、表示を変えない
  expect(within(screen.getByTestId("q-A01")).getByRole("button", { name: "Yes" }).getAttribute("aria-pressed")).toBe("true");
  expect(a01No.getAttribute("aria-pressed")).toBe("false");
  fireEvent.submit(screen.getByRole("form", { name: "Submit judgment" })); // submit が来ても送らない
  expect(save).not.toHaveBeenCalled();
});

test("判定未登録の案件は、保存の前なら、回答を直して結果を選べる（保存の旨と、1回だけの旨を表示）", () => {
  renderReview("CASE-B");
  expect(screen.getByTestId("save-note").textContent).toContain("Registration happens only once");
  expect(screen.queryByTestId("readonly-note")).toBeNull();
  expect(answerButtons().every((b) => !b.disabled)).toBe(true);
  expect(radios().every((r) => !r.disabled)).toBe(true);
  answer("A01", "Yes");
  answer("A01", "No"); // 保存の前なら、直せる
  expect(within(screen.getByTestId("q-A01")).getByRole("button", { name: "No" }).getAttribute("aria-pressed")).toBe("true");
});

test("初回の保存に成功すると、その画面も閲覧のみになる（保存した結果と回答が残り、再保存できない）", async () => {
  renderReview("CASE-D");
  answer("A09", "No");
  choose("Caution");
  submit();
  await screen.findByTestId("registered");
  expect(save).toHaveBeenCalledTimes(1);
  expect(save).toHaveBeenCalledWith("Caution", { A09: "no" }, null);
  expect(screen.queryByRole("button", { name: "Submit judgment" })).toBeNull();
  expect(screen.getByTestId("readonly-note")).toBeTruthy();
  expect(answerButtons().every((b) => b.disabled)).toBe(true);
  expect(radios().every((r) => r.disabled)).toBe(true);
  // 結果と回答は、保存したまま見える
  expect(screen.getByText("Registered result: Caution")).toBeTruthy();
  expect((screen.getByRole("radio", { name: "Caution" }) as HTMLInputElement).checked).toBe(true);
  expect(within(screen.getByTestId("q-A09")).getByRole("button", { name: "No" }).getAttribute("aria-pressed")).toBe("true");
  // 操作を試みても、変わらず、再送もしない
  // 無効な操作を試みても、変わらず、再送もしない（jsdom は無効なラジオのチェック状態を変えるので、結果は表示の文言で確かめる）
  choose("No issue");
  answer("A03", "Yes");
  fireEvent.submit(screen.getByRole("form", { name: "Submit judgment" }));
  expect(within(screen.getByTestId("q-A03")).getByRole("button", { name: "No" }).getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByText("Registered result: Caution")).toBeTruthy();
  expect(save).toHaveBeenCalledTimes(1);
});

test("サーバーが登録済み（409）を返したら、再試行を促さず、押せなくし、結果は登録済みと表示しない", async () => {
  save.mockRejectedValueOnce(new ApiError(409, "The server returned an error (HTTP 409）"));
  renderReview("CASE-B");
  choose("Caution");
  submit();
  const alert = await screen.findByTestId("save-error");
  expect(alert.textContent).toContain("a judgment has already been registered");
  expect(alert.textContent).not.toContain("HTTP 409"); // 原因の重複を出さない
  expect(alert.textContent).not.toContain("again");
  expect(screen.queryByTestId("registered")).toBeNull();
  expect(screen.queryByText(/Registered result/)).toBeNull(); // 保存済みの値は分からないので、表示しない
  expect((screen.getByRole("button", { name: "Submit judgment" }) as HTMLButtonElement).disabled).toBe(true);
  submit();
  fireEvent.submit(screen.getByRole("form", { name: "Submit judgment" }));
  expect(save).toHaveBeenCalledTimes(1);
});

test("確認待ちは食い違いにせず、Noで解消できる", () => {
  renderReview("CASE-D");
  const q = screen.getByTestId("q-A09");
  const yes = within(q).getByRole("button", { name: "Yes" });
  const no = within(q).getByRole("button", { name: "No" });
  expect(q.textContent).toContain("Pending");
  expect([yes.getAttribute("aria-pressed"), no.getAttribute("aria-pressed")]).toEqual(["false", "false"]);
  fireEvent.click(no);
  expect(q.textContent).not.toContain("Pending");
  expect(no.getAttribute("aria-pressed")).toBe("true");
});

test("確認待ちの設問には、理由と、Yes・Noに数えない旨を表示する", () => {
  renderReview("CASE-D");
  const q = screen.getByTestId("q-A09");
  expect(q.textContent).toContain("Could not retrieve the automatic answer from the API key issuance history");
  expect(q.textContent).toContain("It is counted as neither Yes nor No");
});

test("「自動」の印は、人が回答を選ぶと消える", () => {
  renderReview("CASE-D");
  const q = screen.getByTestId("q-A01");
  expect(q.textContent).toContain("Auto");
  fireEvent.click(within(q).getByRole("button", { name: "No" }));
  expect(q.textContent).not.toContain("Auto");
});

test("未回答で結果がないとき、状態バッジは未審査を表示する", () => {
  renderReview("CASE-B");
  expect(screen.getByText("Not reviewed")).toBeTruthy();
});

// ---- CaseDetail（取得と保存のつなぎ） -----------------------------------------------

test("取得中は読み込み中を表示し、取得できたら案件を表示する（A09 の回答も）", async () => {
  stubFetch({ "GET /api/cases/CASE-X": { body: apiCase() } });
  render(<CaseDetail caseId="CASE-X" />);
  expect(screen.getByTestId("case-loading")).toBeTruthy();
  expect(await screen.findByTestId("save-note")).toBeTruthy();
  expect(screen.queryByTestId("case-loading")).toBeNull();
  expect(screen.getByRole("heading", { name: /Fictional summary/ })).toBeTruthy();
  const yes = within(screen.getByTestId("q-A09")).getByRole("button", { name: "Yes" });
  expect(yes.getAttribute("aria-pressed")).toBe("true"); // 受付で取得済みの値
  const a01 = within(screen.getByTestId("q-A01")).getByRole("button", { name: "Yes" });
  expect(a01.getAttribute("aria-pressed")).toBe("false"); // 未回答
});

test("存在しない案件（404）は、見つからない旨と、一覧へ戻る導線を表示する", async () => {
  stubFetch({ "GET /api/cases/CASE-NONE": { status: 404, body: { detail: "Case not found" } } });
  render(<CaseDetail caseId="CASE-NONE" />);
  expect((await screen.findByTestId("case-not-found")).textContent).toContain("CASE-NONE");
  expect(screen.getAllByRole("link", { name: /Back to list/ }).length).toBeGreaterThan(0);
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
  expect((await screen.findByTestId("case-error")).textContent).toContain("Could not connect");
});

test("結果を選んで判定すると、PUT で結果コードを送り、保存済みの結果を表示する", async () => {
  const spy = stubFetch({
    "GET /api/cases/CASE-X": { body: apiCase() },
    "PUT /api/cases/CASE-X/judgment": { status: 204 },
  });
  render(<CaseDetail caseId="CASE-X" />);
  await screen.findByTestId("save-note");
  choose("Caution");
  submit();
  expect((await screen.findByTestId("registered")).textContent).toBe("Saved. Registered result: Caution");
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
  choose("Caution");
  submit();
  expect((await screen.findByTestId("save-error")).textContent).toContain("HTTP 422");
});

test("保存済みの案件を開くと、保存済みの結果を表示する", async () => {
  stubFetch({ "GET /api/cases/CASE-X": { body: apiCase({ result: { result_code: "account_suspension" } }) } });
  render(<CaseDetail caseId="CASE-X" />);
  expect(await screen.findByText("Registered result: Account suspension")).toBeTruthy();
});

test("API経由: 登録済みの案件を開くと、保存済みの結果・回答を見るだけで、PUT を送る操作がない", async () => {
  const questions = apiCase().questions.map((q: { question_code: string }) => ({ ...q, answer: q.question_code === "A01" ? "no" : q.question_code === "A09" ? "yes" : null }));
  const spy = stubFetch({
    "GET /api/cases/CASE-X": { body: apiCase({ questions, judged: true, result: { result_code: "caution", adopted_rule: null } }) },
  });
  render(<CaseDetail caseId="CASE-X" />);
  expect(await screen.findByText("Registered result: Caution")).toBeTruthy();
  expect(screen.getByTestId("readonly-note")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Submit judgment" })).toBeNull();
  expect(answerButtons().every((b) => b.disabled)).toBe(true);
  expect(within(screen.getByTestId("q-A01")).getByRole("button", { name: "No" }).getAttribute("aria-pressed")).toBe("true");
  answer("A01", "Yes");
  fireEvent.submit(screen.getByRole("form", { name: "Submit judgment" }));
  expect(spy.mock.calls.every((call) => (call[1]?.method ?? "GET") === "GET")).toBe(true); // PUT は送らない
});

test("API経由: 初回の保存に成功した画面は、閲覧のみになり、PUT は1回だけ", async () => {
  const spy = stubFetch({
    "GET /api/cases/CASE-X": { body: apiCase() },
    "PUT /api/cases/CASE-X/judgment": { status: 204 },
  });
  render(<CaseDetail caseId="CASE-X" />);
  await screen.findByTestId("save-note");
  choose("Caution");
  submit();
  await screen.findByTestId("registered");
  expect(screen.getByTestId("readonly-note")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Submit judgment" })).toBeNull();
  choose("No issue");
  answer("A01", "Yes");
  fireEvent.submit(screen.getByRole("form", { name: "Submit judgment" }));
  expect(spy.mock.calls.filter(([, init]) => init?.method === "PUT")).toHaveLength(1);
});

test("API経由: 別の操作で登録済みになっていた案件への保存は409で拒否され、その旨を表示し、再送できない", async () => {
  let puts = 0;
  stubFetch({
    "GET /api/cases/CASE-X": { body: apiCase() },
    "PUT /api/cases/CASE-X/judgment": () => (++puts, { status: 409, body: { detail: "judgment has already been registeredの案件は、再保存できません（read-only）" } }),
  });
  render(<CaseDetail caseId="CASE-X" />);
  await screen.findByTestId("save-note");
  choose("Caution");
  submit();
  const alert = await screen.findByTestId("save-error");
  expect(alert.textContent).toContain("a judgment has already been registered");
  expect(screen.queryByTestId("registered")).toBeNull();
  expect(screen.queryByText(/Registered result/)).toBeNull();
  submit();
  expect(puts).toBe(1);
});

test("詳細で、定義にない結果コードが返ったら、エラーを表示し、別の結果に見せない", async () => {
  stubFetch({ "GET /api/cases/CASE-X": { body: apiCase({ result: { result_code: "unknown" } }) } });
  render(<CaseDetail caseId="CASE-X" />);
  expect((await screen.findByTestId("case-error")).textContent).toContain("An unexpected error");
  expect(screen.queryByText(/Registered result/)).toBeNull();
  expect(screen.queryByTestId("save-note")).toBeNull();
});

test("取得の呼び出しは、案件のURLとルールのURLに、1回ずつ行う", async () => {
  const spy = stubFetch({ "GET /api/cases/CASE-X": { body: apiCase() } });
  render(<CaseDetail caseId="CASE-X" />);
  await screen.findByTestId("save-note");
  expect(spy.mock.calls.map((call) => String(call[0])).sort()).toEqual(["/api/cases/CASE-X", "/api/rules"]);
});

test("保存の通信エラー（PUT）は、接続できなかった旨を表示し、保存済みの表示を変えない", async () => {
  stubFetch({
    "GET /api/cases/CASE-X": { body: apiCase() },
    "PUT /api/cases/CASE-X/judgment": new TypeError("fetch failed"),
  });
  render(<CaseDetail caseId="CASE-X" />);
  await screen.findByTestId("save-note");
  choose("Caution");
  submit();
  expect((await screen.findByTestId("save-error")).textContent).toContain("Could not connect");
  expect(screen.queryByTestId("registered")).toBeNull();
  expect(screen.queryByText(/Registered result/)).toBeNull();
});

test("保存に失敗して、もう一度押すと成功する（実際の fetch の経路）", async () => {
  let puts = 0;
  stubFetch({
    "GET /api/cases/CASE-X": { body: apiCase() },
    "PUT /api/cases/CASE-X/judgment": () => (++puts === 1 ? { status: 500 } : { status: 204 }),
  });
  render(<CaseDetail caseId="CASE-X" />);
  await screen.findByTestId("save-note");
  choose("Caution");
  submit();
  await screen.findByTestId("save-error");
  submit();
  expect(await screen.findByTestId("registered")).toBeTruthy();
  expect(screen.queryByTestId("save-error")).toBeNull();
  expect(puts).toBe(2);
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
  await act(async () => pending["/api/rules"](json(apiRules())));
  await act(async () => pending["/api/cases/CASE-B"](json(apiCase({ case_id: "CASE-B", summary: "Summary of case B" }))));
  expect(await screen.findByRole("heading", { name: /Summary of case B/ })).toBeTruthy();
  await act(async () => pending["/api/cases/CASE-A"](json(apiCase({ case_id: "CASE-A", summary: "Summary of case A" }))));
  expect(screen.getByRole("heading", { name: /Summary of case B/ })).toBeTruthy(); // 古い応答で上書きしない
  expect(screen.queryByText(/Summary of case A/)).toBeNull();
});

// ---- S2: 確認待ちの解消と、回答の保存（変更した設問だけを送る） ----------------------------

test("確認待ちを手入力で解消すると、その設問だけを送る。変わらない設問は送らない", async () => {
  renderReview("CASE-D"); // A01=Yes、A03=No、A05=No、A07=Yes、A09=確認待ち
  answer("A01", "Yes"); // 同じ値を選び直しても、変更ではない
  answer("A03", "Yes"); // No → Yes は、変更
  answer("A09", "No"); // 確認待ち → No は、変更
  choose("No issue");
  submit();
  await screen.findByTestId("registered");
  expect(save).toHaveBeenCalledWith("No issue", { A03: "yes", A09: "no" }, null);
});

test("確認待ちのままの設問は、送らない（回答を変えずに結果だけ選んだとき）", async () => {
  renderReview("CASE-D");
  choose("Caution");
  submit();
  await screen.findByTestId("registered");
  expect(save).toHaveBeenCalledWith("Caution", {}, null); // A09 は確認待ちのまま。確認待ちは、送らない
  expect(screen.getByTestId("q-A09").textContent).toContain("Pending");
});

test("保存に失敗したときは、変更が保存済みになっていない（もう一度押すと、同じ変更を送る）", async () => {
  save.mockRejectedValueOnce(new ApiError(500, "The server returned an error (HTTP 500）"));
  renderReview("CASE-D");
  answer("A09", "No");
  choose("Caution");
  submit();
  await screen.findByTestId("save-error");
  submit();
  await screen.findByTestId("registered");
  expect(save).toHaveBeenNthCalledWith(1, "Caution", { A09: "no" }, null);
  expect(save).toHaveBeenNthCalledWith(2, "Caution", { A09: "no" }, null);
});

test("［判定する］を押さないかぎり、何も送らない（途中の自動保存はない）", async () => {
  const spy = stubFetch({ "GET /api/cases/CASE-X": { body: apiCase() } });
  render(<CaseDetail caseId="CASE-X" />);
  await screen.findByTestId("save-note");
  answer("A01", "Yes");
  answer("A02", "No");
  choose("Caution");
  expect(spy.mock.calls.every((call) => (call[1]?.method ?? "GET") === "GET")).toBe(true); // 取得（GET）だけ
  expect(spy).toHaveBeenCalledTimes(2); // 案件とルール
});

test("保存しなかった変更は、開き直すと残らない（取得した値を表示する）", async () => {
  stubFetch({ "GET /api/cases/CASE-X": { body: apiCase() } });
  const first = render(<CaseDetail caseId="CASE-X" />);
  await screen.findByTestId("save-note");
  answer("A01", "Yes");
  expect(within(screen.getByTestId("q-A01")).getByRole("button", { name: "Yes" }).getAttribute("aria-pressed")).toBe("true");
  first.unmount();
  render(<CaseDetail caseId="CASE-X" />); // 開き直す
  await screen.findByTestId("save-note");
  expect(within(screen.getByTestId("q-A01")).getByRole("button", { name: "Yes" }).getAttribute("aria-pressed")).toBe("false");
});

test("取得失敗の案件（A09が確認待ち）は、確認待ちの表示と、理由、件数を出す", async () => {
  const questions = [{ question_code: "A09", answer: "pending" }];
  stubFetch({ "GET /api/cases/CASE-X": { body: apiCase({ questions }) } });
  render(<CaseDetail caseId="CASE-X" />);
  await screen.findByTestId("save-note");
  const q = screen.getByTestId("q-A09");
  expect(q.textContent).toContain("Pending");
  expect(q.textContent).toContain("The materials could not be confirmed"); // APIは理由を返さないため、既定の文言
  expect(q.textContent).toContain("It is counted as neither Yes nor No");
  expect(screen.getByText("Pending 1")).toBeTruthy();
  expect(screen.getByText("Pending data")).toBeTruthy(); // 結果がなく、確認待ちがあるときの状態
});

test("取得した案件で、回答を変えて判定すると、PUT の本文に、変更した設問だけが入る", async () => {
  const spy = stubFetch({
    "GET /api/cases/CASE-X": { body: apiCase() }, // A09=Yes、ほかは未回答
    "PUT /api/cases/CASE-X/judgment": { status: 204 },
  });
  render(<CaseDetail caseId="CASE-X" />);
  await screen.findByTestId("save-note");
  answer("A01", "No");
  answer("A09", "Yes"); // 同じ値。変更ではない
  choose("Caution");
  submit();
  await screen.findByTestId("registered");
  const put = spy.mock.calls.find(([, init]) => init?.method === "PUT")!;
  expect(JSON.parse((put[1] as RequestInit).body as string)).toEqual({ result_code: "caution", answers: { A01: "no" } });
});

test("確認待ちを手入力で解消すると、確認待ちの表示・件数・状態が変わる", () => {
  renderReview("CASE-D"); // Yes 2、No 2、確認待ち 1、未回答 7
  expect(screen.getByText("Pending 1")).toBeTruthy();
  expect(screen.getByText("Pending data")).toBeTruthy();
  answer("A09", "No");
  const q = screen.getByTestId("q-A09");
  expect(q.textContent).not.toContain("Pending");
  expect(q.textContent).not.toContain("It is counted as neither Yes nor No");
  expect(within(q).getByRole("button", { name: "No" }).getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByText("Pending 0")).toBeTruthy();
  expect(screen.getByText("No 3")).toBeTruthy();
  expect(screen.queryByText("Pending data")).toBeNull();
  expect(screen.getByText("Not reviewed")).toBeTruthy();
});

test("変えてから、元の値に戻した設問は、変更として送らない", async () => {
  renderReview("CASE-D"); // A03 は No
  answer("A03", "Yes");
  answer("A03", "No"); // 元に戻す
  choose("Caution");
  submit();
  await screen.findByTestId("registered");
  expect(save).toHaveBeenCalledWith("Caution", {}, null);
});

test("回答を変えて保存に失敗したら、エラーを表示し、保存済みの結果は表示しない。回答の変更は画面に残る", async () => {
  save.mockRejectedValueOnce(new ApiError(500, "The server returned an error (HTTP 500）"));
  renderReview("CASE-D");
  answer("A09", "No");
  choose("Caution");
  submit();
  await screen.findByTestId("save-error");
  expect(screen.queryByTestId("registered")).toBeNull();
  expect(screen.queryByText(/Registered result/)).toBeNull();
  const no = within(screen.getByTestId("q-A09")).getByRole("button", { name: "No" });
  expect(no.getAttribute("aria-pressed")).toBe("true"); // 入力した値は、消さない
});

test("保存している間は、回答・結果の操作を受け付けない（送った内容と、画面の内容がずれない）", async () => {
  let finish: () => void = () => {};
  save.mockReturnValueOnce(new Promise<void>((resolve) => (finish = resolve)));
  renderReview("CASE-D");
  answer("A09", "No");
  choose("Caution");
  submit();
  await screen.findByRole("button", { name: "Saving…" });
  expect(answerButtons().every((b) => b.disabled)).toBe(true);
  expect(radios().every((r) => r.disabled)).toBe(true);
  answer("A03", "Yes"); // 保存中に、別の設問を変えようとしても、変わらない
  expect(within(screen.getByTestId("q-A03")).getByRole("button", { name: "No" }).getAttribute("aria-pressed")).toBe("true");
  expect((screen.getByRole("radio", { name: "Caution" }) as HTMLInputElement).checked).toBe(true);
  await act(async () => finish());
  await screen.findByTestId("registered");
  expect(save).toHaveBeenCalledTimes(1);
  expect(save).toHaveBeenCalledWith("Caution", { A09: "no" }, null);
  expect(within(screen.getByTestId("q-A03")).getByRole("button", { name: "No" }).getAttribute("aria-pressed")).toBe("true");
});

// ---- ルールはAPIから取得する（受入例1〜3を、API経由の画面で） ------------------------------

test("取得したルールで、照合の通常・グレーが決まる（例1→例2。A01を訂正）", async () => {
  stubFetch({ "GET /api/cases/CASE-X": { body: apiCase() } });
  render(<CaseDetail caseId="CASE-X" />);
  await screen.findByTestId("save-note");
  answer("A01", "Yes");
  answer("A03", "Yes");
  expect(rules(display)).toEqual(["normal", "normal", "gray"]); // 例1
  answer("A01", "No");
  expect(rules(display)).toEqual(["gray", "gray", "normal"]); // 例2
  expect(screen.getByTestId("rule-count").textContent).toBe("1 of 3 rules match");
});

test("ルールが、APIが返したものだけになる（固定のルールを使わない）", async () => {
  const { conditions } = apiRules()[0];
  stubFetch({
    "GET /api/cases/CASE-X": { body: apiCase() },
    "GET /api/rules": { body: [{ rule_id: "R77", revision: 3, result_code: "account_deletion", conditions }] },
  });
  render(<CaseDetail caseId="CASE-X" />);
  await screen.findByTestId("save-note");
  expect(screen.getByTestId("rule-R77")).toBeTruthy();
  expect(screen.queryByTestId("rule-R01")).toBeNull();
  expect(screen.getByTestId("rule-count").textContent).toBe("1 of 1 rules match");
});

test("ルールが0件でも、4つの結果をすべて選べて、保存できる", async () => {
  const spy = stubFetch({
    "GET /api/cases/CASE-X": { body: apiCase() },
    "GET /api/rules": { body: [] },
    "PUT /api/cases/CASE-X/judgment": { status: 204 },
  });
  render(<CaseDetail caseId="CASE-X" />);
  await screen.findByTestId("save-note");
  for (const name of ["Account deletion", "Account suspension", "Caution", "No issue"]) {
    expect(screen.getByRole("radio", { name })).toBeTruthy();
  }
  // 0件の表示。照合を絞る意味がないので、未回答の設問を「省略できる」とはしない。
  expect(screen.getByTestId("rule-count").textContent).toBe("0 of 0 rules match");
  expect(screen.queryByTestId("rule-R01")).toBeNull();
  expect(screen.getByTestId("q-A01").className).not.toContain("gray");
  expect(screen.getByRole("radio", { name: "Account deletion" }).hasAttribute("disabled")).toBe(false);
  choose("No issue");
  submit();
  await screen.findByText("Registered result: No issue");
  const put = spy.mock.calls.find((call) => call[1]?.method === "PUT")!;
  expect(JSON.parse(String(put[1]?.body))).toEqual({ result_code: "no_issue" });
});

test("例3: A01=Yes・A03=No で全行グレー。それでも、結果と、変更した回答（A01・A03）を、API経由で保存する", async () => {
  const spy = stubFetch({
    "GET /api/cases/CASE-X": { body: apiCase() },
    "PUT /api/cases/CASE-X/judgment": { status: 204 },
  });
  render(<CaseDetail caseId="CASE-X" />);
  await screen.findByTestId("save-note");
  answer("A01", "Yes");
  answer("A03", "No");
  expect(rules(display)).toEqual(["gray", "gray", "gray"]);
  expect(screen.getByTestId("rule-count").textContent).toBe("0 of 3 rules match");
  choose("No issue");
  submit();
  await screen.findByText("Registered result: No issue");
  const put = spy.mock.calls.find((call) => call[1]?.method === "PUT")!;
  expect(JSON.parse(String(put[1]?.body))).toEqual({ result_code: "no_issue", answers: { A01: "yes", A03: "no" } });
});

test("ルールを取得できなければ、エラーを表示し、判定の操作を出さない", async () => {
  stubFetch({ "GET /api/cases/CASE-X": { body: apiCase() }, "GET /api/rules": { status: 500 } });
  render(<CaseDetail caseId="CASE-X" />);
  const alert = await screen.findByTestId("rules-error");
  expect(alert.textContent).toContain("HTTP 500");
  expect(screen.queryByRole("button", { name: "Submit judgment" })).toBeNull();
});

test("ルールの形が不正（条件の値が yes／no でない）・通信できないときも、エラーを表示し、判定の操作を出さない", async () => {
  const lacking = { A05: "maybe" };
  stubFetch({
    "GET /api/cases/CASE-X": { body: apiCase() },
    "GET /api/rules": { body: [{ rule_id: "R09", revision: 1, result_code: "caution", conditions: lacking }] },
  });
  const { unmount } = render(<CaseDetail caseId="CASE-X" />);
  expect((await screen.findByTestId("rules-error")).textContent).toContain("Could not retrieve");
  expect(screen.queryByRole("button", { name: "Submit judgment" })).toBeNull();
  unmount();
  stubFetch({ "GET /api/cases/CASE-X": { body: apiCase() }, "GET /api/rules": new TypeError("fetch failed") });
  render(<CaseDetail caseId="CASE-X" />);
  expect((await screen.findByTestId("rules-error")).textContent).toContain("Could not connect");
  expect(screen.queryByRole("button", { name: "Submit judgment" })).toBeNull();
});

test("案件が見つからない（404）ときは、ルールの取得に失敗していても、見つからない旨を表示する", async () => {
  stubFetch({ "GET /api/cases/CASE-NONE": { status: 404, body: {} }, "GET /api/rules": { status: 500 } });
  render(<CaseDetail caseId="CASE-NONE" />);
  expect(await screen.findByTestId("case-not-found")).toBeTruthy();
  expect(screen.queryByTestId("rules-error")).toBeNull();
});

test("ルールの取得中は、読み込み中を表示する（案件だけ取得できていても）", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) =>
      url === "/api/rules"
        ? new Promise<Response>(() => {})
        : Promise.resolve(new Response(JSON.stringify(apiCase()), { status: 200 })),
    ),
  );
  render(<CaseDetail caseId="CASE-X" />);
  // 案件の応答が届いたあと（fetch が両方呼ばれたあと）も、ルールが未着なら、読み込み中のまま。
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  await act(async () => {});
  expect(screen.getByTestId("case-loading")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Submit judgment" })).toBeNull();
});

test("一部の設問だけに条件があるルールは、その設問だけで照合し、ほかは「問わない」と表示する", async () => {
  stubFetch({
    "GET /api/cases/CASE-X": { body: apiCase() }, // A09=Yes
    "GET /api/rules": {
      body: [
        { rule_id: "P1", revision: 1, result_code: "caution", conditions: { A03: "no", A09: "yes" } },
        { rule_id: "P2", revision: 1, result_code: "no_issue", conditions: { A09: "no" } },
      ],
    },
  });
  render(<CaseDetail caseId="CASE-X" />);
  await screen.findByTestId("save-note");
  // A09=Yes: P1 は食い違わない（A03は未回答）、P2 は食い違う。
  expect(display("P1")).toBe("normal");
  expect(display("P2")).toBe("gray");
  expect(screen.getByTestId("cond-P1-A01").textContent).toContain("Not required");
  expect(screen.getByTestId("cond-P1-A03").textContent).toContain("No");
  expect(matched("P1", "A09")).toBe(true);
  expect(matched("P1", "A01")).toBe(false); // 問わない設問は緑にしない
  answer("A01", "No"); // 問わない設問の回答では、行は変わらない
  expect(display("P1")).toBe("normal");
  answer("A03", "Yes"); // 条件のある設問が食い違えば、グレー
  expect(display("P1")).toBe("gray");
});

// ---- ルールの採用（受入例8〜10） ---------------------------------------------------------

const adoptButton = (r: string) => screen.queryByTestId(`adopt-${r}`);
const adoptNote = () => screen.getByTestId("adopted-note").textContent;
const clickAdopt = (r: string) => fireEvent.click(screen.getByTestId(`adopt-${r}`));

test("例9: 「このルールを採用」は、通常表示の行にだけ出る。グレーの行には出ない", () => {
  renderReview("CASE-B");
  answer("A01", "Yes");
  answer("A03", "Yes");
  expect(adoptButton("R01")).toBeTruthy();
  expect(adoptButton("R02")).toBeTruthy();
  expect(adoptButton("R03")).toBeNull(); // グレー
});

test("例8: 採用して、結果は違うもの（問題なし）を選んで判定すると、採用ルールの版も一緒に送る", async () => {
  renderReview("CASE-B");
  answer("A01", "Yes");
  answer("A03", "Yes");
  clickAdopt("R01"); // R01 の結果は「注意」
  expect(screen.getByTestId("adopted-R01")).toBeTruthy();
  expect(adoptNote()).toContain("R01 (revision 1)");
  choose("No issue");
  submit();
  await screen.findByTestId("registered");
  expect(save).toHaveBeenCalledWith("No issue", { A01: "yes", A03: "yes" }, { ruleId: "R01", revision: 1 });
});

test("結果を選んだだけでは、採用ルールを送らない", async () => {
  renderReview("CASE-B");
  answer("A01", "Yes");
  answer("A03", "Yes");
  choose("Caution"); // R01 と同じ結果でも、採用にはならない
  submit();
  await screen.findByTestId("registered");
  expect(save).toHaveBeenCalledWith("Caution", { A01: "yes", A03: "yes" }, null);
  expect(adoptNote()).toContain("Adopted rule: none");
});

test("例10: 採用したあと、回答の訂正でその行がグレーになると、採用が外れる。戻しても、採用し直すまで戻らない", async () => {
  renderReview("CASE-B");
  answer("A01", "Yes");
  answer("A03", "Yes");
  clickAdopt("R01");
  answer("A01", "No"); // R01 がグレーになる
  expect(display("R01")).toBe("gray");
  expect(screen.queryByTestId("adopted-R01")).toBeNull();
  expect(adoptNote()).toContain("Adopted rule: none");
  answer("A01", "Yes"); // 戻して、R01 が通常に戻っても、採用は戻らない
  expect(display("R01")).toBe("normal");
  expect(screen.queryByTestId("adopted-R01")).toBeNull();
  choose("Caution");
  submit();
  await screen.findByTestId("registered");
  expect(save).toHaveBeenCalledWith("Caution", { A01: "yes", A03: "yes" }, null); // 外した状態で保存すると、採用なし
});

test("採用した行が、ほかの設問の訂正で、食い違わないままなら、採用は外れない", () => {
  renderReview("CASE-B");
  answer("A01", "Yes");
  answer("A03", "Yes");
  clickAdopt("R01");
  answer("A02", "Yes"); // R01 は A02=Yes を期待している。食い違わない
  expect(screen.getByTestId("adopted-R01")).toBeTruthy();
});

test("採用済みの行で、もう一度押すと、採用が外れる（判定前なら外せる）。別の行を採用すると、置き換わる", async () => {
  renderReview("CASE-B");
  answer("A01", "Yes");
  answer("A03", "Yes");
  clickAdopt("R01");
  expect(adoptButton("R01")?.textContent).toBe("Unadopt this rule");
  clickAdopt("R01");
  expect(screen.queryByTestId("adopted-R01")).toBeNull();
  clickAdopt("R01");
  clickAdopt("R02"); // 採用できるのは1つだけ。置き換わる
  expect(screen.queryByTestId("adopted-R01")).toBeNull();
  expect(screen.getByTestId("adopted-R02")).toBeTruthy();
  choose("Caution");
  submit();
  await screen.findByTestId("registered");
  expect(save).toHaveBeenCalledWith("Caution", { A01: "yes", A03: "yes" }, { ruleId: "R02", revision: 1 });
});

test("保存済みの採用ルールがある登録済みの案件は、採用中と表示するだけで、外す・付け替える操作も、送る操作もない", () => {
  const c = { ...fixture("CASE-B"), answers: { A01: "yes", A03: "yes" } as const, result: "No issue" as const, adopted: { ruleId: "R01", revision: 1 } };
  render(<Review c={c} rules={DEMO_RULES} save={save} />);
  expect(screen.getByTestId("adopted-R01")).toBeTruthy();
  expect(adoptNote()).toContain("R01 (revision 1)");
  expect(adoptButton("R01")).toBeNull(); // 採用を外す・付け替える操作がない
  expect(adoptButton("R02")).toBeNull();
  expect(screen.queryByRole("button", { name: "Submit judgment" })).toBeNull();
  expect(save).not.toHaveBeenCalled();
});

test("登録済みの案件で、保存済みの採用ルールが、いまのルール（版）にない・グレーのときは、採用中とせず、操作も出さない", () => {
  const base = { ...fixture("CASE-B"), answers: { A01: "yes", A03: "yes" } as const, result: "Caution" as const };
  const { unmount } = render(<Review c={{ ...base, adopted: { ruleId: "R01", revision: 9 } }} rules={DEMO_RULES} save={save} />);
  expect(screen.queryByTestId("adopted-R01")).toBeNull(); // 版が違う
  expect(screen.queryByRole("button", { name: "Submit judgment" })).toBeNull();
  unmount();
  render(<Review c={{ ...base, adopted: { ruleId: "R03", revision: 1 } }} rules={DEMO_RULES} save={save} />);
  expect(screen.queryByTestId("adopted-R03")).toBeNull(); // R03 はグレー
  expect(adoptNote()).toContain("Adopted rule: none");
  expect(save).not.toHaveBeenCalled();
});

test("API経由: 採用して判定すると、PUT の本文に rule_id と rule_revision が入る。再表示で、採用中と表示する", async () => {
  const spy = stubFetch({
    "GET /api/cases/CASE-X": { body: apiCase() },
    "PUT /api/cases/CASE-X/judgment": { status: 204 },
  });
  render(<CaseDetail caseId="CASE-X" />);
  await screen.findByTestId("save-note");
  answer("A01", "Yes");
  answer("A03", "Yes");
  clickAdopt("R01");
  choose("No issue");
  submit();
  await screen.findByTestId("registered");
  const put = spy.mock.calls.find((call) => call[1]?.method === "PUT")!;
  expect(JSON.parse(String(put[1]?.body))).toEqual({
    result_code: "no_issue",
    answers: { A01: "yes", A03: "yes" },
    rule_id: "R01",
    rule_revision: 1,
  });
  cleanup();
  stubFetch({
    "GET /api/cases/CASE-X": {
      body: apiCase({
        questions: apiCase().questions.map((q: { question_code: string }) => ({
          ...q,
          answer: q.question_code === "A01" || q.question_code === "A03" ? "yes" : q.question_code === "A09" ? "yes" : null,
        })),
        result: { result_code: "no_issue", adopted_rule: { rule_id: "R01", rule_revision: 1 } },
      }),
    },
  });
  render(<CaseDetail caseId="CASE-X" />);
  await screen.findByTestId("save-note");
  expect(screen.getByTestId("adopted-R01")).toBeTruthy();
  expect(screen.getByText("Registered result: No issue")).toBeTruthy();
});

test("サーバーが採用ルールを拒否した（422）ときは、エラーを表示し、保存済みの表示を変えない", async () => {
  stubFetch({
    "GET /api/cases/CASE-X": { body: apiCase() },
    "PUT /api/cases/CASE-X/judgment": { status: 422, body: { detail: "採用ルールが存在しません: R01 の版 1" } },
  });
  render(<CaseDetail caseId="CASE-X" />);
  await screen.findByTestId("save-note");
  answer("A01", "Yes");
  answer("A03", "Yes");
  clickAdopt("R01");
  choose("Caution");
  submit();
  const error = await screen.findByTestId("save-error");
  expect(error.textContent).toContain("HTTP 422");
  expect(screen.queryByTestId("registered")).toBeNull();
  expect(screen.queryByText(/Registered result/)).toBeNull();
});

test("登録済みの案件で、回答の訂正を試みても、保存済みの採用は外れない（訂正できない）", () => {
  const c = { ...fixture("CASE-B"), answers: { A01: "yes", A03: "yes" } as const, result: "No issue" as const, adopted: { ruleId: "R01", revision: 1 } };
  render(<Review c={c} rules={DEMO_RULES} save={save} />);
  answer("A01", "No"); // 無効なボタン。表示も、採用も変わらない
  expect(screen.getByTestId("adopted-R01")).toBeTruthy();
  expect(display("R01")).toBe("normal");
  expect(save).not.toHaveBeenCalled();
});

test("採用して保存に失敗したら、採用の表示を保ち、もう一度押すと、同じ採用ルールを送る", async () => {
  save.mockRejectedValueOnce(new ApiError(422, "The server returned an error (HTTP 422）"));
  renderReview("CASE-B");
  answer("A01", "Yes");
  answer("A03", "Yes");
  clickAdopt("R01");
  choose("Caution");
  submit();
  await screen.findByTestId("save-error");
  expect(screen.getByTestId("adopted-R01")).toBeTruthy();
  submit();
  await screen.findByTestId("registered");
  expect(save).toHaveBeenNthCalledWith(1, "Caution", { A01: "yes", A03: "yes" }, { ruleId: "R01", revision: 1 });
  expect(save).toHaveBeenNthCalledWith(2, "Caution", { A01: "yes", A03: "yes" }, { ruleId: "R01", revision: 1 });
});

test("「このルールを採用」は、役割と名前で取れる。グレー行の中にはない", () => {
  renderReview("CASE-B");
  answer("A01", "Yes");
  answer("A03", "Yes");
  expect(screen.getAllByRole("button", { name: "Adopt this rule" })).toHaveLength(2); // R01・R02
  expect(within(screen.getByTestId("rule-R03")).queryByRole("button")).toBeNull();
});

// ---- 採用ルールと同じ結果に、印を付ける（結果は自動で選ばない） --------------------------

const hintOn = () => ["Account deletion", "Account suspension", "Caution", "No issue"].filter((r) => within(screen.getByTestId(`outcome-${r}`)).queryByTestId("outcome-hint") !== null);

test("ルールを採用すると、そのルールの結果の選択肢に印が付く。結果は自動で選ばない", () => {
  renderReview("CASE-B");
  answer("A01", "Yes");
  answer("A03", "Yes");
  expect(hintOn()).toEqual([]); // 採用前は、印なし
  clickAdopt("R01"); // R01 の結果は「注意」
  expect(hintOn()).toEqual(["Caution"]);
  expect((screen.getByRole("radio", { name: "Caution" }) as HTMLInputElement).checked).toBe(false); // 選ばれてはいない
  expect(screen.getByRole("radio", { name: "Caution" }).getAttribute("aria-describedby")).toBe("outcome-hint");
});

test("採用を付け替えると、印も移る。採用を外す・グレーになると、印は消える。選んだ結果とは独立", () => {
  renderReview("CASE-B");
  answer("A01", "Yes");
  answer("A03", "Yes");
  clickAdopt("R01");
  choose("No issue"); // 採用ルールと違う結果を選ぶ
  expect(hintOn()).toEqual(["Caution"]); // 印は、選んだ結果ではなく、採用ルールの結果
  expect((screen.getByRole("radio", { name: "No issue" }) as HTMLInputElement).checked).toBe(true);
  clickAdopt("R02"); // R02 の結果は「問題なし」
  expect(hintOn()).toEqual(["No issue"]);
  clickAdopt("R02"); // 採用を外す
  expect(hintOn()).toEqual([]);
  clickAdopt("R01");
  answer("A01", "No"); // R01 がグレーになり、採用が外れる
  expect(hintOn()).toEqual([]);
});

test("結果の選択肢の名前は、印があっても変わらない（結果の名前だけで取れる）", () => {
  renderReview("CASE-B");
  answer("A01", "Yes");
  answer("A03", "Yes");
  clickAdopt("R01");
  expect(screen.getByRole("radio", { name: "Caution" })).toBeTruthy();
});

// ---- 廃止済みの設問（管理者が廃止。保存済みの設問文・回答は、読み取り専用で表示する） ----------------------

const retiredA13 = { id: "A13", prompt: "対象期間にAPIキーが失効されたか？（架空）", answer: "yes" as const };

test("判定未登録: 廃止済みの設問は回答の対象外（Yes／Noなし）で、保存済みの設問文と回答を確認できる。最新ルールで照合する", () => {
  render(<Review c={{ ...fixture("CASE-B"), retiredQuestions: [retiredA13] }} rules={DEMO_RULES} save={save} />);
  const section = screen.getByTestId("retired-questions");
  expect(within(section).getByTestId("retired-A13").textContent).toContain("対象期間にAPIキーが失効されたか？（架空）");
  expect(within(section).getByTestId("retired-A13").textContent).toContain("Deactivated");
  expect(screen.getByTestId("retired-answer-A13").textContent).toContain("Saved answer: Yes");
  expect(screen.queryByTestId("q-A13")).toBeNull(); // 回答の設問の一覧にはない
  expect(within(section).queryByRole("button")).toBeNull(); // 新たな回答の操作がない
  expect(screen.getByTestId("retired-note").textContent).toContain("Matching uses the latest rules");
  expect(screen.getByTestId("retired-note").textContent).toContain("no longer open to new answers");
  expect(screen.getByTestId("progress").textContent).toBe("0 of 12 answered");
  // 照合は、最新のルール（R01〜R03）のまま動く。
  answer("A01", "Yes");
  answer("A03", "Yes");
  expect(rules(display)).toEqual(["normal", "normal", "gray"]);
});

test("判定登録済み: 登録時の結果と、保存済みの設問文・回答を表示する", () => {
  render(<Review c={{ ...fixture("CASE-E"), retiredQuestions: [{ ...retiredA13, answer: "no" }] }} rules={DEMO_RULES} save={save} />);
  expect(screen.getByText("Registered result: No issue")).toBeTruthy();
  expect(screen.getByTestId("retired-note").textContent).toContain("Showing the result at registration and the saved question text and answers");
  expect(screen.getByTestId("retired-A13").textContent).toContain("対象期間にAPIキーが失効されたか？（架空）");
  expect(screen.getByTestId("retired-answer-A13").textContent).toContain("Saved answer: No");
});

test("廃止済みで回答が未回答・確認待ちの設問は、その状態をそのまま表示する", () => {
  render(
    <Review
      c={{ ...fixture("CASE-B"), retiredQuestions: [{ id: "A13", prompt: "未回答の設問？" }, { id: "A14", prompt: "Pendingの設問？", answer: "pending" }] }}
      rules={DEMO_RULES}
      save={save}
    />,
  );
  expect(screen.getByTestId("retired-answer-A13").textContent).toContain("Unanswered");
  expect(screen.getByTestId("retired-answer-A14").textContent).toContain("Pending");
});

test("廃止済みの設問がない案件は、廃止済みの一覧を出さない", () => {
  renderReview("CASE-B");
  expect(screen.queryByTestId("retired-questions")).toBeNull();
  expect(screen.getByTestId("progress").textContent).toBe("0 of 12 answered");
});

test("廃止済みの設問（A01〜A12のいずれか）は、回答の一覧から外れ、保存する回答に入らない", async () => {
  render(
    <Review
      c={{ ...fixture("CASE-B"), answers: { A01: "yes" }, retiredQuestions: [{ id: "A05", prompt: "廃止した設問？", answer: "no" }] }}
      rules={DEMO_RULES}
      save={save}
    />,
  );
  expect(screen.queryByTestId("q-A05")).toBeNull();
  expect(screen.getByTestId("progress").textContent).toBe("1 of 11 answered");
  answer("A03", "Yes");
  choose("Caution");
  submit();
  await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
  expect(save).toHaveBeenCalledWith("Caution", { A03: "yes" }, null);
});

test("CaseDetail: APIが返す廃止済みの設問を、保存済みの設問文・回答つきで表示し、保存では送らない", async () => {
  const questions = [
    ...Array.from({ length: 12 }, (_, i) => ({ question_code: `A${String(i + 1).padStart(2, "0")}`, answer: null, retired: false })),
    { question_code: "A13", prompt: "対象期間にAPIキーが失効されたか？（架空）", answer: "yes", retired: true },
  ];
  const spy = stubFetch({
    "GET /api/cases/CASE-X": { body: apiCase({ questions }) },
    "PUT /api/cases/CASE-X/judgment": { status: 204 },
  });
  render(<CaseDetail caseId="CASE-X" />);
  expect(await screen.findByTestId("retired-A13")).toBeTruthy();
  expect(screen.getByTestId("retired-answer-A13").textContent).toContain("Saved answer: Yes");
  answer("A01", "Yes");
  choose("Caution");
  submit();
  await screen.findByTestId("registered");
  const put = spy.mock.calls.find(([, init]) => init?.method === "PUT");
  expect(JSON.parse(String(put?.[1]?.body))).toEqual({ result_code: "caution", answers: { A01: "yes" } });
});
