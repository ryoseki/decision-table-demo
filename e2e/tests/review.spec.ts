import { expect, test, type APIRequestContext } from "@playwright/test";

import { backendUrl } from "../playwright.config";

// 個別審査の業務E2E（受入例7）。案件を受付（システムからのAPI呼び出し。画面にはない）→ 画面で開く →
// 結果を選んで［判定する］→ 開き直して、保存済みの値を確認する。DBまで通す。
// 画面は相対パス（/api/...）でAPIを呼ぶ。開発とE2Eでは、Viteが backend サービスへ中継する。
// 照合・確認待ち・訂正などの細かい挙動は、画面の部品テスト（frontend/src/*.test.tsx）で確認する。
// モバイル幅の横はみ出しは、実際のレイアウト計算が要るため、ここで確認する。
const intake = async (request: APIRequestContext, userId: string) => {
  const response = await request.post(`${backendUrl}/api/intake/cases`, {
    data: {
      summary: "心当たりのないログインについて問い合わせが届いた。（架空・E2E）",
      user: { user_id: userId },
      inquiry: { subject: "心当たりのないログイン", body: "架空の問い合わせ。" },
    },
  });
  expect(response.status()).toBe(201);
  return (await response.json()) as { case_id: string; answers: Record<string, string> };
};

const pressed = (page: import("@playwright/test").Page, q: string, v: "Yes" | "No") =>
  page.getByTestId(`q-${q}`).getByRole("button", { name: v, exact: true });

test("例7: 受付 → 画面で開く → 結果を選んで判定 → 開き直すと、保存済みの結果と取得済みの回答を表示する", async ({ page, request }) => {
  const { case_id, answers } = await intake(request, "456"); // 偶数の利用者IDは、A09がYes（モックの契約）
  expect(answers).toEqual({ A09: "yes" });

  await page.goto(`/#/cases/${case_id}`);
  await expect(page.getByTestId("save-note")).toBeVisible();
  await expect(pressed(page, "A09", "Yes")).toHaveAttribute("aria-pressed", "true"); // 受付で取得した値
  await expect(page.getByText(/登録済みの結果/)).toHaveCount(0); // まだ判定していない

  await page.getByRole("radio", { name: "注意" }).check();
  await page.getByRole("button", { name: "判定する" }).click();
  await expect(page.getByTestId("registered")).toHaveText("保存しました。登録結果: 注意");

  await page.reload(); // 開き直す
  await expect(page.getByText("登録済みの結果: 注意")).toBeVisible();
  await expect(page.getByRole("radio", { name: "注意" })).toBeChecked();
  await expect(pressed(page, "A09", "Yes")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("registered")).toHaveCount(0); // 「保存しました」は、保存した直後だけ

  // 画面ではなく、APIとDBに保存されている。
  const saved = await (await request.get(`${backendUrl}/api/cases/${case_id}`)).json();
  expect(saved.result.result_code).toBe("caution");
  // 再表示でHTTPモックの呼び出しが増えないことは、ここでは確認できない（モックを呼ぶのはサーバー側で、
  // ブラウザの通信には現れない）。実HTTPのAPI統合テスト（backend/tests/test_db_store.py の
  // test_s1_... と test_list_cases_...）が、モックの呼び出し回数を数えて確認している。
});

test("一覧に、受付した案件が出て、そこから詳細を開ける", async ({ page, request }) => {
  const { case_id } = await intake(request, "458");
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "審査対象一覧" })).toBeVisible();
  const open = page.getByRole("link", { name: new RegExp(`審査を開く.*${case_id}`) });
  await expect(open).toBeVisible();
  await open.click();
  await expect(page).toHaveURL(new RegExp(`#/cases/${case_id}$`));
  await expect(page.getByTestId("save-note")).toBeVisible();
});

test("存在しない案件は、見つからない旨を表示する（APIの404を、画面で扱う）", async ({ page }) => {
  await page.goto("/#/cases/CASE-NOT-EXIST");
  await expect(page.getByTestId("case-not-found")).toBeVisible();
  await expect(page.getByTestId("case-not-found")).toContainText("CASE-NOT-EXIST");
});

test("モバイル幅で横にはみ出さない", async ({ page, request }) => {
  const { case_id } = await intake(request, "460");
  await page.setViewportSize({ width: 390, height: 800 });
  await page.goto(`/#/cases/${case_id}`);
  await expect(page.getByTestId("save-note")).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});
