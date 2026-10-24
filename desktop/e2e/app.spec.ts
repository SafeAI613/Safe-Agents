import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

// One shared manager and agent, so the scenarios run in order.
test.describe.configure({ mode: "serial" });

let page: Page;

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
  await expect(page.getByTestId("cost")).toContainText("120");
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
  await expect(page.getByTestId("cost")).toContainText("120");

  await open(/^bar/);
  await expect(messages).toContainText("Rejected by the user.");
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
  await card.getByRole("button", { name: "יומן" }).click();
  await expect(page.getByRole("dialog", { name: "יומן – foo_agent" })).toContainText("starting");
  await page.getByRole("button", { name: "סגירת החלון" }).click();
  await card.getByRole("button", { name: "עצירה" }).click();
  await expect(card).toContainText("כבוי");
});
