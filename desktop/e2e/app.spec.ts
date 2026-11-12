import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

// One shared manager and agent, so the scenarios run in order.
test.describe.configure({ mode: "serial" });

let page: Page;

const GUARDED_COST = /עלות השיחה: \$[\d.]+ · [1-9]20 טוקנים בקלט/;

test.beforeAll(async ({ browser }) => {
  page = await browser.newPage();
});

test.afterAll(async () => {
  await page.close();
});

test("first run asks for the OpenRouter key and validates it", async () => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "ברוכים הבאים ל-Safe Agents" })).toBeVisible();

  await page.getByLabel("מפתח OpenRouter").fill("sk-or-wrong");
  await page.getByRole("button", { name: "שמירה והמשך" }).click();
  await expect(page.getByRole("alert")).toContainText("נדחה");

  await page.getByLabel("מפתח OpenRouter").fill("sk-or-e2e-good");
  await page.getByRole("button", { name: "שמירה והמשך" }).click();
  await expect(page.getByRole("heading", { name: "האייג'נטים שלי" })).toBeVisible();
});

test("chat: file, streaming steps, approval and cost", async () => {
  const card = page.getByTestId("agent-foo_agent");
  await expect(card).toContainText("Foo Agent");
  await card.getByRole("button", { name: "פתיחת שיחה" }).click();

  const composer = page.getByLabel("הודעה לאייג'נט");
  await expect(composer).toBeEnabled();
  await page.locator('input[type="file"]').setInputFiles({
    name: "notes.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("lorem ipsum"),
  });
  await expect(page.locator(".attachments")).toContainText("notes.txt");

  await composer.fill("foo");
  await page.getByRole("button", { name: "שליחה" }).click();

  const messages = page.getByTestId("messages");
  await expect(messages).toContainText("קבצים מצורפים");
  await expect(messages).toContainText(/inbox.*notes\.txt/);
  await expect(messages).toContainText("Lorem ipsum dolor sit amet.");
  const approval = page.getByRole("alertdialog", { name: "האייג'נט מבקש אישור" });
  await expect(approval).toContainText("publish_answer");
  await expect(page.getByTestId("run-status")).toHaveText("ממתין לאישור שלך");
  await approval.getByRole("button", { name: "אישור" }).click();
  await expect(messages).toContainText("Approved.");
  await expect(approval).toBeHidden();
  await expect(page.getByTestId("run-status")).toBeHidden();

  await expect(page.getByTestId("cost")).toContainText("עלות השיחה");
  // 120 from the agent's model, plus 100 for each content-guard check of the conversation.
  await expect(page.getByTestId("cost")).toHaveText(GUARDED_COST);
});

test("export writes the conversation to a file", async () => {
  await page.getByRole("button", { name: "ייצוא שיחה" }).click();
  const notice = page.getByText(/השיחה נשמרה: /);
  await expect(notice).toBeVisible();
  const path = (await notice.textContent())?.replace("השיחה נשמרה: ", "").trim() ?? "";
  const content = readFileSync(path, "utf-8");
  expect(content).toContain("# שיחה עם Foo Agent");
  expect(content).toContain("Approved.");
});

test("rejecting the approval is passed to the agent", async () => {
  await page.getByRole("button", { name: "שיחה חדשה" }).click();
  await page.getByLabel("הודעה לאייג'נט").fill("bar");
  await page.getByRole("button", { name: "שליחה" }).click();
  const approval = page.getByRole("alertdialog", { name: "האייג'נט מבקש אישור" });
  await approval.getByRole("button", { name: "דחייה" }).click();
  await expect(page.getByTestId("messages")).toContainText("Rejected by the user.");
  await expect(page.getByTestId("messages")).not.toContainText("Approved.");
});

test("earlier conversations can be reopened", async () => {
  const picker = page.getByLabel("שיחות קודמות");
  await expect(picker.locator("option")).toHaveCount(3); // new + "foo" + "bar"
  const open = async (prefix: RegExp) => {
    const value = await picker.locator("option", { hasText: prefix }).getAttribute("value");
    await picker.selectOption(value ?? "");
  };
  const messages = page.getByTestId("messages");

  await open(/^foo/);
  await expect(messages).toContainText("Approved.");
  await expect(messages).not.toContainText("Rejected by the user.");
  await expect(page.getByTestId("cost")).toHaveText(GUARDED_COST);

  await open(/^bar/);
  await expect(messages).toContainText("Rejected by the user.");
});

test("content guard: a question outside the agent's task is not sent to it", async () => {
  await page.getByRole("button", { name: "שיחה חדשה" }).click();
  await page.getByLabel("הודעה לאייג'נט").fill("off-topic lorem");
  await page.getByRole("button", { name: "שליחה" }).click();
  await expect(page.getByRole("alert")).toContainText("אינה קשורה לתפקיד של האייג'נט");
  await expect(page.getByTestId("messages")).not.toContainText("Lorem ipsum dolor sit amet.");
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
});

test("content guard: an answer against the policy is hidden live and in history", async () => {
  await page.getByRole("button", { name: "שיחה חדשה" }).click();
  await page.getByLabel("הודעה לאייג'נט").fill("repeat: hidden-answer lorem");
  await page.getByRole("button", { name: "שליחה" }).click();
  const messages = page.getByTestId("messages");
  const notice = "התוכן הוסתר: הוא אינו עומד במדיניות התוכן של הפלטפורמה.";
  await expect(messages).toContainText(notice);
  const approval = page.getByRole("alertdialog", { name: "האייג'נט מבקש אישור" });
  await expect(approval).toContainText(notice); // the request repeats the answer
  await expect(approval).not.toContainText("hidden-answer lorem");
  await expect(messages.locator(".message-ai", { hasText: "hidden-answer" })).toHaveCount(0);
  await approval.getByRole("button", { name: "דחייה" }).click();
  await expect(messages).toContainText("Rejected by the user.");

  const picker = page.getByLabel("שיחות קודמות");
  const value = await picker.locator("option", { hasText: /^repeat/ }).getAttribute("value");
  await page.getByRole("button", { name: "שיחה חדשה" }).click();
  await picker.selectOption(value ?? "");
  await expect(messages).toContainText(notice);
  await expect(messages.locator(".message-ai", { hasText: "hidden-answer" })).toHaveCount(0);
});

test("help panel shows the agent's examples", async () => {
  await page.getByRole("button", { name: "עזרה" }).click();
  const help = page.getByRole("complementary", { name: "עזרה" });
  await help.getByRole("button", { name: "Lorem ipsum dolor sit amet?" }).click();
  await expect(page.getByLabel("הודעה לאייג'נט")).toHaveValue("Lorem ipsum dolor sit amet?");
});

test("settings show the key and the recorded usage", async () => {
  await page.getByRole("button", { name: "הגדרות" }).click();
  await expect(page.getByText("e2e key")).toBeVisible();
  await expect(page.locator("table")).toContainText("meta-llama/llama-3.1-70b-instruct");

  const filter = page.getByRole("article", { name: "סינון תוכן" });
  await expect(filter).toContainText("ברירת המחדל של האפליקציה"); // no policy.json in the e2e catalog
  await expect(filter).toContainText("openai/gpt-4o-mini");

  const support = page.getByRole("article", { name: "אודות ותמיכה" });
  await expect(support).toContainText("0.1.0");
  await support.getByRole("button", { name: "יומן המערכת" }).click();
  await expect(page.getByRole("dialog", { name: "יומן המערכת" })).toContainText("GET /settings");
  await page.getByRole("button", { name: "סגירת החלון" }).click();
});

test("store lists the catalog", async () => {
  await page.getByRole("button", { name: "חנות" }).click();
  const card = page.getByTestId("catalog-bar_agent");
  await expect(card).toContainText("Bar Agent");
  await expect(card.getByRole("button", { name: "התקנה" })).toBeVisible();
});

test("install from a ZIP file: failure is explained, success shows the agent", async () => {
  test.setTimeout(600_000); // a real install downloads the agent's dependencies
  const work = fileURLToPath(new URL("./.tmp", import.meta.url));
  const zipInput = page.getByTestId("install-zip");

  await zipInput.setInputFiles(`${work}/foo-agent.zip`);
  let dialog = page.getByRole("dialog", { name: "התקנה מקובץ: foo-agent.zip" });
  await expect(dialog).toContainText("לא עבר את בדיקות הקטלוג");
  await dialog.getByRole("button", { name: "התקנה" }).click();
  await expect(dialog).toContainText("already installed");
  await dialog.getByRole("button", { name: "סגירה" }).click();

  await zipInput.setInputFiles(`${work}/zip-agent.zip`);
  dialog = page.getByRole("dialog", { name: "התקנה מקובץ: zip-agent.zip" });
  await dialog.getByRole("button", { name: "התקנה" }).click();
  await expect(dialog).toContainText("Zip Agent 0.1.0 installed", { timeout: 300_000 });
  await dialog.getByRole("button", { name: "לאייג'נטים שלי" }).click();
  await expect(page.getByTestId("agent-zip_agent")).toContainText("Zip Agent");
});

test("agents can be stopped and their log inspected", async () => {
  await page.getByRole("button", { name: "האייג'נטים שלי" }).click();
  const card = page.getByTestId("agent-foo_agent");
  await expect(card).toContainText("פועל");
  await expect(card).toContainText("עדכון זמין: גרסה 2.0.0");
  await card.getByRole("button", { name: "יומן" }).click();
  await expect(page.getByRole("dialog", { name: "יומן – foo_agent" })).toContainText("starting");
  await page.getByRole("button", { name: "סגירת החלון" }).click();
  await card.getByRole("button", { name: "עצירה" }).click();
  await expect(card).toContainText("כבוי");
});
