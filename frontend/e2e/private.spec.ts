import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Page } from "@playwright/test";

/* PRIVATE visual QA and performance on the confidential pack. Skipped unless
 * PROMOLENS_TEST_ZIP is set. Screenshots and metrics go to ../runs/screens (gitignored),
 * because they show screenplay text. Nothing here is committed or deployed. */
const ZIP = process.env.PROMOLENS_TEST_ZIP;
const ROOT = fileURLToPath(new URL("../..", import.meta.url));
const BUNDLE = resolve(ROOT, "runs", "ai_bundle.json");
const OUT = resolve(ROOT, "runs", "screens");

test.skip(!ZIP || !existsSync(resolve(ROOT, ZIP ?? "")) || !existsSync(BUNDLE), "private pack or AI bundle not available");
test.skip(({ isMobile }) => isMobile, "covered by the mobile block below");

const shot = (page: Page, name: string) => page.screenshot({ path: resolve(OUT, `${name}.png`) });

/** Click, then wait for two animation frames: approximates click-to-next-paint. */
async function timeClick(page: Page, selector: string, nth = 0): Promise<number> {
  return page.evaluate(
    async ({ selector, nth }) => {
      const el = document.querySelectorAll<HTMLElement>(selector)[nth];
      const t = performance.now();
      el.click();
      await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
      return performance.now() - t;
    },
    { selector, nth },
  );
}

async function uploadRealPack(page: Page) {
  await page.goto("/");
  await page.getByTestId("screenplay-input").setInputFiles(resolve(ROOT, ZIP!));
  await page.getByTestId("bundle-input").setInputFiles(BUNDLE);
  const t = Date.now();
  await page.getByRole("button", { name: /^Analyze/ }).click();
  await expect(page.getByRole("tablist", { name: "Ranking" })).toBeVisible({ timeout: 30_000 });
  return Date.now() - t;
}

test("real pack: AI-reviewed and full-show workspace, metrics", async ({ page }) => {
  mkdirSync(OUT, { recursive: true });
  const metrics: Record<string, number | string> = {};
  for (const width of [1440, 1366]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");
    await shot(page, `upload-${width}`);
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  metrics.upload_to_workspace_ms = await uploadRealPack(page);
  await expect(page.getByRole("tab", { name: /AI-reviewed/ })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("status")).toHaveText(/^\s*Saved$/);
  const aiRows = await page.getByRole("option").count();
  metrics.ai_rows_rendered = aiRows;
  await shot(page, "ai-workspace-1440");
  await page.getByRole("main", { name: "Candidate detail" }).evaluate((el) => el.scrollBy(0, 640));
  await shot(page, "ai-detail-timeline-1440");
  await page.getByRole("main", { name: "Candidate detail" }).evaluate((el) => el.scrollBy(0, 900));
  await shot(page, "ai-detail-excerpt-1440");

  // candidate switching (click to next paint), averaged over several rows
  const switches: number[] = [];
  for (let i = 1; i <= 6; i++) switches.push(await timeClick(page, "[role=option]", i));
  metrics.candidate_switch_ms_avg = Math.round(switches.reduce((a, b) => a + b) / switches.length);
  metrics.candidate_switch_ms_max = Math.round(Math.max(...switches));

  metrics.view_switch_ms = Math.round(await timeClick(page, "[role=tab]", 1));
  await expect(page.getByRole("tab", { name: /Full show/ })).toHaveAttribute("aria-selected", "true");
  await shot(page, "full-show-1440");
  metrics.full_show_rows_in_dom = await page.getByRole("option").count();

  // search across 788 candidates: time from input to next paint
  metrics.search_ms = Math.round(
    await page.evaluate(async () => {
      const input = document.querySelector<HTMLInputElement>("input[type=search]")!;
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
      const t = performance.now();
      setter.call(input, "father");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
      await new Promise((r) => setTimeout(r, 0));
      await new Promise((r) => requestAnimationFrame(r));
      return performance.now() - t;
    }),
  );
  metrics.search_result_label = (await page.locator("text=/\\d+ shown/").first().textContent()) ?? "";
  await page.getByRole("searchbox", { name: "Search candidates" }).fill("");

  await page.getByRole("button", { name: /^Filters/ }).click();
  await shot(page, "filters-1440");
  await page.getByLabel("Episode").selectOption("12");
  await page.keyboard.press("Escape");
  await shot(page, "filtered-episode-1440");

  // refresh restore with the full 1.8 MB document
  const t = Date.now();
  await page.reload();
  await expect(page.getByRole("tablist", { name: "Ranking" })).toBeVisible();
  metrics.refresh_restore_ms = Date.now() - t;
  metrics.stored_doc_note = "IndexedDB restore of the full analysis";

  await page.setViewportSize({ width: 1366, height: 768 });
  await page.getByRole("tab", { name: /AI-reviewed/ }).click();
  await shot(page, "ai-workspace-1366");
  writeFileSync(resolve(OUT, "metrics.json"), JSON.stringify(metrics, null, 2));
  console.log(JSON.stringify(metrics));
});

test("real pack: mobile list and detail", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
  const page = await context.newPage();
  mkdirSync(OUT, { recursive: true });
  await page.goto("/");
  await shot(page, "mobile-upload");
  await uploadRealPack(page);
  await shot(page, "mobile-ai-list");
  await page.getByRole("option").first().click();
  await shot(page, "mobile-ai-detail");
  await page.getByRole("main", { name: "Candidate detail" }).evaluate((el) => el.scrollBy(0, 700));
  await shot(page, "mobile-ai-detail-timeline");
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await context.close();
});
