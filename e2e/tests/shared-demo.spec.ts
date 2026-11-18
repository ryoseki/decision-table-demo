import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

import { backendUrl, frontendUrl } from "../playwright.config";

const SHARED_BACKEND = "http://e2e-shared-backend:8000";
const PROMPT = "E2E用の架空の共有設問";

async function openSharedQuestions(page: Page, address: string | null = "192.0.2.10") {
  // エッジ（CloudFront Functions）が付与する専用ヘッダーを、テストのHTTP境界で再現する。
  await page.route("**/api/admin/**", async (route) => {
    const request = route.request();
    const response = await route.fetch({
      url: SHARED_BACKEND + new URL(request.url()).pathname,
      headers: { ...request.headers(), ...(address === null ? {} : { "X-Demo-Viewer-Ip": address }) },
    });
    await route.fulfill({ response });
  });
  await page.goto("/#/admin/questions");
}

test("共有DBへの承認が別ブラウザに見え、利用枠超過では共有一覧が変わらない", async ({ page, browser }) => {
  await openSharedQuestions(page);
  await page.getByLabel("変更の依頼（自然文）").fill(PROMPT);
  await page.getByRole("button", { name: "追加案を作る" }).click();
  await expect(page.getByTestId("proposal")).toBeVisible();
  await page.getByRole("button", { name: "Yes", exact: true }).click();
  await expect(page.getByTestId("proposal-notice")).toBeVisible();

  const another = await browser.newContext({ baseURL: frontendUrl });
  try {
    const visitor = await another.newPage();
    await openSharedQuestions(visitor);
    const current = visitor.getByRole("region", { name: "現行のデモの設問一覧" });
    await expect(current.getByText(PROMPT)).toBeVisible();
    await expect(visitor.getByTestId("mock-note")).toContainText("他の来訪者にも見えます");
    const count = await current.getByRole("row").count();
    await visitor.getByLabel("変更の依頼（自然文）").fill("利用枠超過の架空の依頼");
    await visitor.getByRole("button", { name: "追加案を作る" }).click();
    await expect(visitor.getByRole("alert")).toContainText("上限");
    await expect(visitor.getByTestId("proposal")).toHaveCount(0);
    await expect(current.getByRole("row")).toHaveCount(count);
  } finally {
    await another.close();
  }
});

// 共有DBへの実際のルール承認の後に、判定未登録の案件を開く。照合の一覧はスタブにせず、共有バックエンドの
// 実際の GET /api/rules を使う。登録済みの判定（保存した結果・回答・採用ルール）は、承認で変わらない。
// 判定は上のテストと同じ共有DBを使うため、同じ設定の中で、上から順に実行する（workers: 1）。
async function intake(request: APIRequestContext, userId: string) {
  const response = await request.post(`${backendUrl}/api/intake/cases`, {
    data: {
      summary: "共有ルール承認の確認用の案件。（架空・E2E）",
      user: { user_id: userId },
      inquiry: { subject: "心当たりのないログイン", body: "架空の問い合わせ。" },
    },
  });
  expect(response.status()).toBe(201);
  return ((await response.json()) as { case_id: string }).case_id;
}

test("共有DBで承認した判定ルールが、判定未登録の案件の照合に使われ、登録済みの判定は変わらない", async ({ page, request }) => {
  const judgedId = await intake(request, "456");
  const undecidedId = await intake(request, "457");
  expect((await request.put(`${backendUrl}/api/cases/${judgedId}/judgment`, {
    data: { result_code: "caution", rule_id: "R01", rule_revision: 1 },
  })).status()).toBe(204);
  const judgment = async () => (await (await request.get(`${SHARED_BACKEND}/api/cases/${judgedId}`)).json()) as unknown;
  const before = await judgment();
  const ruleIds = async () =>
    ((await (await request.get(`${SHARED_BACKEND}/api/rules`)).json()) as { rule_id: string }[]).map((r) => r.rule_id);
  expect(await ruleIds()).not.toContain("R04");

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const response = await route.fetch({
      url: SHARED_BACKEND + url.pathname + url.search,
      headers: { ...route.request().headers(), "X-Demo-Viewer-Ip": "192.0.2.20" },
    });
    await route.fulfill({ response });
  });
  await page.goto("/#/admin/rules");
  await page.getByLabel("変更の依頼（自然文）").fill("E2E用の架空のルールを追加して");
  await page.getByRole("button", { name: "案を作る" }).click();
  await expect(page.getByTestId("proposal")).toBeVisible();
  await page.getByRole("button", { name: "Yes", exact: true }).click();
  await expect(page.getByTestId("proposal-notice")).toBeVisible();

  expect(await ruleIds()).toContain("R04"); // 共有DBに承認済みの最新の一覧
  await page.goto(`/#/cases/${undecidedId}`);
  await expect(page.getByTestId("rule-R04")).toBeVisible(); // 実際の /api/rules で照合している
  expect(await judgment()).toEqual(before);
  await page.goto(`/#/cases/${judgedId}`);
  await expect(page.getByText("登録済みの結果: 注意")).toBeVisible();
});


const TEMPLATE_HEADING = "AIを経由せずに反映する変更の依頼(完全一致)";

test("未知の依頼はBedrockで差分を作り、Yesで反映すると自動登録される。同じ依頼はAIを呼ばず、削除すると通常の生成に戻る", async ({ page }) => {
  const prompt = "E2E用の再利用候補";
  await openSharedQuestions(page, null);
  const input = page.getByLabel("変更の依頼（自然文）");
  const propose = page.getByRole("button", { name: "追加案を作る" });
  const summary = page.getByText(TEMPLATE_HEADING);
  // 定型文の一覧は、初期状態で閉じている。
  await expect(summary).toBeVisible();
  await expect(page.locator("details.template-panel")).not.toHaveAttribute("open", "");
  await input.fill(prompt);
  await propose.click();
  await expect(page.getByTestId("source-badge")).toHaveAttribute("data-source", "bedrock");
  await page.getByRole("button", { name: "Yes", exact: true }).click();
  await expect(page.getByTestId("proposal-notice")).toBeVisible();
  // 登録の操作は画面にない。反映と同時に自動登録され、開いた一覧に現れる。
  await expect(page.getByRole("button", { name: "再利用に登録" })).toHaveCount(0);
  await summary.click();
  const panel = page.locator("details.template-panel");
  await expect(panel.getByText(prompt, { exact: false })).toBeVisible();
  // 同じ文言は、再入力時の状態で既に操作済み（同じ設問がある）なので、重複して反映せず理由を出す。
  // 利用枠は使い切っているため、Bedrockを呼んでいれば「上限」になる。理由が出るのはアプリ内の経路。
  await propose.click();
  await expect(page.getByRole("alert")).toContainText("既に存在");
  await expect(page.getByTestId("proposal")).toHaveCount(0);
  // 削除すると、次の依頼から通常の生成（Bedrock）に戻る。
  await panel.getByRole("button", { name: prompt + "を定型文から削除" }).click();
  await expect(panel.getByText(prompt, { exact: false })).toHaveCount(0);
  await propose.click();
  await expect(page.getByRole("alert")).toContainText("上限");
});

test("初期の定型文は、Bedrockを呼ばずに差分を作り、Yesで反映する。同じ依頼は、現在の状態で操作できない理由を出す", async ({ page }) => {
  await openSharedQuestions(page, "192.0.2.30");
  await page.goto("/#/admin/rules");
  const prompt = "ルールR03を削除して";
  await page.getByLabel("変更の依頼（自然文）").fill(prompt);
  await page.getByRole("button", { name: "案を作る" }).click();
  await expect(page.getByTestId("source-badge")).toHaveAttribute("data-source", "template");
  await expect(page.getByTestId("proposal")).toContainText("R03");
  await page.getByRole("button", { name: "Yes", exact: true }).click();
  await expect(page.getByTestId("proposal-notice")).toBeVisible();
  // 反映済み。もう一度入力しても、重複して反映せず、理由を出す（Bedrockの枠も使わない）。
  await page.getByLabel("変更の依頼（自然文）").fill(prompt);
  await page.getByRole("button", { name: "案を作る" }).click();
  await expect(page.getByRole("alert")).toContainText("R03");
  await expect(page.getByTestId("proposal")).toHaveCount(0);
  // 反映後も、定型文は一覧に残る（削除されない限り使える）。
  await page.getByText(TEMPLATE_HEADING).click();
  await expect(page.locator("details.template-panel").getByText(prompt, { exact: false })).toBeVisible();
});
