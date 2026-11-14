import { expect, test, type APIRequestContext } from "@playwright/test";

import { backendUrl } from "../playwright.config";

// 個別審査の業務E2E（受入例7）。案件を受付（システムからのAPI呼び出し。画面にはない）→ 画面で開く →
// 結果を選んで［判定する］→ 開き直して、保存済みの値を確認する。DBまで通す。
// 画面は相対パス（/api/...）でAPIを呼ぶ。開発とE2Eでは、Viteが backend サービスへ中継する。
// 照合・確認待ち・訂正などの細かい挙動は、画面の部品テスト（frontend/src/*.test.tsx）で確認する。
// モバイル幅の横はみ出しは、実際のレイアウト計算が要るため、ここで確認する。
// failQuestions: デモ用の失敗指示（demo_controls.fault_questions）。その設問の自動回答の取得を失敗させ、確認待ちにする。
const intake = async (request: APIRequestContext, userId: string, failQuestions: string[] = []) => {
  const response = await request.post(`${backendUrl}/api/intake/cases`, {
    data: {
      summary: "心当たりのないログインについて問い合わせが届いた。（架空・E2E）",
      user: { user_id: userId },
      inquiry: { subject: "心当たりのないログイン", body: "架空の問い合わせ。" },
      ...(failQuestions.length > 0 ? { demo_controls: { fault_questions: failQuestions } } : {}),
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

const click = (page: import("@playwright/test").Page, q: string, v: "Yes" | "No") => pressed(page, q, v).click();
// 表示が変わるまで待つ（再描画が遅れても、不安定にならない）。
const expectDisplay = (page: import("@playwright/test").Page, rule: string, value: "normal" | "gray") =>
  expect(page.getByTestId(`rule-${rule}`)).toHaveAttribute("data-display", value);

test("例1〜3: DBのルールを取得して照合する。訂正で表示が変わり、全行グレーでも結果を選んで保存でき、開き直すと保存済み", async ({ page, request }) => {
  // 画面が使うルールは、DBのルール（開発用の架空R01〜R03）をAPIから取得したもの。
  const rules = await (await request.get(`${backendUrl}/api/rules`)).json();
  expect(rules.map((r: { rule_id: string }) => r.rule_id)).toEqual(["R01", "R02", "R03"]);

  const { case_id } = await intake(request, "456");
  await page.goto(`/#/cases/${case_id}`);
  await expect(page.getByTestId("save-note")).toBeVisible();

  // 例1: A01=Yes、A03=Yes → R01・R02が通常、R03がグレー
  await click(page, "A01", "Yes");
  await click(page, "A03", "Yes");
  await expectDisplay(page, "R01", "normal");
  await expectDisplay(page, "R02", "normal");
  await expectDisplay(page, "R03", "gray");

  // 例2: A01をNoに訂正 → R01・R02がグレー、R03が通常
  await click(page, "A01", "No");
  await expectDisplay(page, "R01", "gray");
  await expectDisplay(page, "R02", "gray");
  await expectDisplay(page, "R03", "normal");

  // 例3: A01=Yes、A03=No → 全行グレー。それでも結果「問題なし」を選んで保存できる
  await click(page, "A01", "Yes");
  await click(page, "A03", "No");
  for (const rule of ["R01", "R02", "R03"]) await expectDisplay(page, rule, "gray");
  await page.getByRole("radio", { name: "問題なし" }).check();
  await page.getByRole("button", { name: "判定する" }).click();
  await expect(page.getByTestId("registered")).toHaveText("保存しました。登録結果: 問題なし");

  await page.reload(); // 開き直す
  await expect(page.getByText("登録済みの結果: 問題なし")).toBeVisible();
  await expect(pressed(page, "A01", "Yes")).toHaveAttribute("aria-pressed", "true");
  await expect(pressed(page, "A03", "No")).toHaveAttribute("aria-pressed", "true");
  for (const rule of ["R01", "R02", "R03"]) await expectDisplay(page, rule, "gray");

  const saved = await (await request.get(`${backendUrl}/api/cases/${case_id}`)).json();
  expect(saved.result.result_code).toBe("no_issue");
  const byCode = Object.fromEntries(saved.questions.map((q: { question_code: string; answer: string | null }) => [q.question_code, q.answer]));
  expect(byCode).toMatchObject({ A01: "yes", A03: "no", A09: "yes" });
});

test("例8〜10: 通常表示の行だけ採用でき、結果と違っても保存され、再表示で残る。訂正でグレーになると、採用は外れ、外した状態で保存すると空になる", async ({ page, request }) => {
  const { case_id } = await intake(request, "456");
  await page.goto(`/#/cases/${case_id}`);
  await expect(page.getByTestId("save-note")).toBeVisible();
  await click(page, "A01", "Yes");
  await click(page, "A03", "Yes");

  // 例9: グレー表示のR03には、「このルールを採用」が出ない。通常表示のR01・R02には出る。
  await expectDisplay(page, "R03", "gray");
  await expect(page.getByTestId("adopt-R03")).toHaveCount(0);
  await expect(page.getByTestId("adopt-R01")).toBeVisible();

  // 例8: R01（結果は注意）を採用して、結果は違う「問題なし」を選んで保存。開き直すと、採用ルールが残る。
  await page.getByTestId("adopt-R01").click();
  await expect(page.getByTestId("adopted-R01")).toBeVisible();
  // 採用したR01の結果（注意）の選択肢に、印が付く。結果は自動では選ばれない。
  await expect(page.getByTestId("outcome-注意").getByTestId("outcome-hint")).toBeVisible();
  await expect(page.getByRole("radio", { name: "注意" })).not.toBeChecked();
  await page.getByRole("radio", { name: "問題なし" }).check();
  await page.getByRole("button", { name: "判定する" }).click();
  await expect(page.getByTestId("registered")).toHaveText("保存しました。登録結果: 問題なし");
  await page.reload();
  await expect(page.getByTestId("adopted-R01")).toBeVisible();
  await expect(page.getByTestId("adopted-note")).toContainText("R01（版 1）");
  await expect(page.getByText("登録済みの結果: 問題なし")).toBeVisible();
  let saved = await (await request.get(`${backendUrl}/api/cases/${case_id}`)).json();
  expect(saved.result.result_code).toBe("no_issue"); // 結果は、選んだものがそのまま
  expect(saved.result.adopted_rule).toEqual({ rule_id: "R01", rule_revision: 1 });

  // 例10: A01をNoに訂正すると、R01がグレーになり、採用が自動で外れる。外した状態で保存すると、採用ルールは空。
  await click(page, "A01", "No");
  await expectDisplay(page, "R01", "gray");
  await expect(page.getByTestId("adopted-R01")).toHaveCount(0);
  await expect(page.getByTestId("adopted-note")).toContainText("採用ルール: なし");
  await page.getByRole("button", { name: "判定する" }).click();
  await expect(page.getByTestId("registered")).toBeVisible();
  await page.reload();
  await expect(page.getByTestId("adopted-R01")).toHaveCount(0);
  saved = await (await request.get(`${backendUrl}/api/cases/${case_id}`)).json();
  expect(saved.result.adopted_rule).toBeNull();
  expect(saved.result.result_code).toBe("no_issue");
});

test("例4〜6: 取得に失敗した案件は確認待ち。結果だけ保存しても確認待ちのまま。手入力で解消して保存すると上書きされ、保存しない変更は残らない", async ({ page, request }) => {
  const { case_id, answers } = await intake(request, "458", ["A09"]); // A09 の取得を失敗させる
  expect(answers).toEqual({ A09: "pending" });
  const q09 = page.getByTestId("q-A09");

  // 例4: 確認待ちのまま、結果「注意」を選んで［判定する］。開き直しても、確認待ちのまま。
  await page.goto(`/#/cases/${case_id}`);
  await expect(q09).toContainText("確認待ち");
  await expect(pressed(page, "A09", "Yes")).toHaveAttribute("aria-pressed", "false");
  await expect(pressed(page, "A09", "No")).toHaveAttribute("aria-pressed", "false");
  await page.getByRole("radio", { name: "注意" }).check();
  await page.getByRole("button", { name: "判定する" }).click();
  await expect(page.getByTestId("registered")).toHaveText("保存しました。登録結果: 注意");
  await page.reload();
  await expect(page.getByText("登録済みの結果: 注意")).toBeVisible();
  await expect(q09).toContainText("確認待ち");

  // 例5: 資料で確認し、手入力でNo。結果「問題なし」で［判定する］。開き直すと、Noと問題なしで上書きされている。
  await pressed(page, "A09", "No").click();
  await expect(q09).not.toContainText("確認待ち");
  await page.getByRole("radio", { name: "問題なし" }).check();
  await page.getByRole("button", { name: "判定する" }).click();
  await expect(page.getByTestId("registered")).toHaveText("保存しました。登録結果: 問題なし");
  await page.reload();
  await expect(page.getByText("登録済みの結果: 問題なし")).toBeVisible();
  await expect(pressed(page, "A09", "No")).toHaveAttribute("aria-pressed", "true");
  await expect(q09).not.toContainText("確認待ち");
  const saved = await (await request.get(`${backendUrl}/api/cases/${case_id}`)).json();
  expect(saved.result.result_code).toBe("no_issue");
  expect(saved.questions.find((q: { question_code: string }) => q.question_code === "A09").answer).toBe("no");

  // 例6: 保存したあとに画面で変更し、［判定する］を押さずに開き直す。最後に保存した値が表示される。
  await pressed(page, "A01", "Yes").click();
  await page.getByRole("radio", { name: "アカウント削除" }).check();
  await page.reload();
  await expect(pressed(page, "A01", "Yes")).toHaveAttribute("aria-pressed", "false"); // 未保存の回答は残らない
  await expect(page.getByText("登録済みの結果: 問題なし")).toBeVisible(); // 結果も、最後の保存値
  await expect(page.getByRole("radio", { name: "問題なし" })).toBeChecked();
  await expect(pressed(page, "A09", "No")).toHaveAttribute("aria-pressed", "true"); // 保存済みの A09 も、そのまま
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
