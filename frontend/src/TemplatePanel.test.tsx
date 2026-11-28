// 実行: npm test（Vitest + jsdom）。AIを経由せずに反映する変更の依頼（定型文）の一覧と削除の部品を確認する。APIはモックで、サーバーには接続しない。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { TEMPLATE_HEADING, TEMPLATE_NOTE, TemplatePanel } from "./TemplatePanel";
import { stubFetch } from "./testing";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const PATH = "/api/admin/proposal-templates/rule";
const deleteButton = (prompt: string) => screen.findByRole("button", { name: `Delete ${prompt} from saved requests` });

test("見出しと案内の文言は、指定のまま。初期状態は閉じていて、見出しの直下に小さく案内する", async () => {
  expect(TEMPLATE_HEADING).toBe("Saved requests (exact match, no AI call)");
  expect(TEMPLATE_NOTE).toBe("If an unintended change proposal appears, delete the matching request.");
  stubFetch({ ["GET " + PATH]: { body: { prompts: [] } } });
  const { container } = render(<TemplatePanel target="rule" refreshKey={0} />);
  const details = container.querySelector("details")!;
  expect(details.open).toBe(false);
  expect(details.firstElementChild!.tagName).toBe("SUMMARY");
  expect(details.firstElementChild!.textContent).toBe(TEMPLATE_HEADING);
  expect(details.children[1].querySelector("small")!.textContent).toBe(TEMPLATE_NOTE);
  await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
});

test("対象ごとの定型文一覧を出す。登録の操作は一覧にない", async () => {
  const spy = stubFetch({ ["GET " + PATH]: { body: { prompts: ["Remove rule R03", "別の言い回し"] } } });
  render(<TemplatePanel target="rule" refreshKey={0} />);
  await deleteButton("Remove rule R03");
  expect(screen.getByText("別の言い回し", { exact: false })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /register/ })).toBeNull();
  expect(spy.mock.calls.map(([url]) => url)).toEqual([PATH]);
});

test("削除は文言だけを送り、成功したら一覧から消す", async () => {
  const bodies: unknown[] = [];
  stubFetch({
    ["GET " + PATH]: { body: { prompts: ["削除する依頼", "残す依頼"] } },
    ["POST " + PATH + "/remove"]: (init) => {
      bodies.push(JSON.parse(init?.body as string));
      return { body: { prompts: ["残す依頼"] } };
    },
  });
  render(<TemplatePanel target="rule" refreshKey={0} />);
  fireEvent.click(await deleteButton("削除する依頼"));
  await waitFor(() => expect(screen.queryByText("削除する依頼", { exact: false })).toBeNull());
  expect(screen.getByText("残す依頼", { exact: false })).toBeTruthy();
  expect(bodies).toEqual([{ prompt: "削除する依頼" }]);
});

test("refreshKey が変わると、一覧を取得し直す（Yesで自動登録された依頼が現れる）", async () => {
  let reads = 0;
  stubFetch({
    ["GET " + PATH]: () => {
      reads += 1;
      return { body: { prompts: reads === 1 ? [] : ["自動登録された依頼"] } };
    },
  });
  const { rerender } = render(<TemplatePanel target="rule" refreshKey={0} />);
  await waitFor(() => expect(reads).toBe(1));
  rerender(<TemplatePanel target="rule" refreshKey={1} />);
  await deleteButton("自動登録された依頼");
  expect(reads).toBe(2);
});

test("削除に失敗したら、エラーを出し、その定型文を一覧に残す", async () => {
  stubFetch({
    ["GET " + PATH]: { body: { prompts: ["残すべき依頼"] } },
    ["POST " + PATH + "/remove"]: { status: 500, body: { detail: "削除できませんでした" } },
  });
  render(<TemplatePanel target="rule" refreshKey={0} />);
  fireEvent.click(await deleteButton("残すべき依頼"));
  const message = (await screen.findByRole("alert")).textContent ?? "";
  expect(message).toContain("HTTP 500");
  expect(message).toContain("saved requests");
  expect(message).not.toContain("Question"); // 判定ルールの画面でも使うため
  expect(screen.getByText("残すべき依頼", { exact: false })).toBeTruthy();
});

test("一覧を取得できないときは、エラーを出し、削除ボタンを出さない", async () => {
  stubFetch({ ["GET " + PATH]: { status: 500, body: { detail: "取得できません" } } });
  render(<TemplatePanel target="rule" refreshKey={0} />);
  const message = (await screen.findByRole("alert")).textContent ?? "";
  expect(message).toContain("HTTP 500");
  expect(message).not.toContain("Question");
  expect(screen.queryByRole("button", { name: /Delete/ })).toBeNull();
});

test("通信できないときは、削除の有無を言い切らず、設問一覧とは書かない", async () => {
  stubFetch({
    ["GET " + PATH]: { body: { prompts: ["依頼"] } },
    ["POST " + PATH + "/remove"]: new Error("offline"),
  });
  render(<TemplatePanel target="rule" refreshKey={0} />);
  fireEvent.click(await deleteButton("依頼"));
  const message = (await screen.findByRole("alert")).textContent ?? "";
  expect(message).toContain("It is unknown whether");
  expect(message).not.toContain("Question");
});

test("送信中は、同じ操作を二重に送らない", async () => {
  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => { release = resolve; });
  const spy = stubFetch({
    ["GET " + PATH]: { body: { prompts: ["依頼"] } },
    ["POST " + PATH + "/remove"]: () => { throw new Error("unused"); },
  });
  // 削除の応答を保留して、送信中の状態を作る。
  const base = spy.getMockImplementation()!;
  let removes = 0;
  spy.mockImplementation(async (input, init) => {
    if (String(input).endsWith("/remove")) {
      removes += 1;
      await gate;
      return new Response(JSON.stringify({ prompts: [] }), { status: 200 });
    }
    return base(input, init);
  });
  render(<TemplatePanel target="rule" refreshKey={0} />);
  const button = await deleteButton("依頼");
  fireEvent.click(button);
  await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(true));
  fireEvent.click(button);
  release();
  await waitFor(() => expect(screen.queryByRole("button", { name: "Delete request from saved requests" })).toBeNull());
  expect(removes).toBe(1);
});
