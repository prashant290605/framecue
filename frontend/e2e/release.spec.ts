import { expect, test } from "@playwright/test";

// Release checks, safe to run against the public deployment (E2E_BASE_URL). Uses only the
// fictional sample. Asserts: same-origin traffic only (no model or third-party calls), no
// cookies or localStorage, both views, the timeline, deep-link reload, and export -> import.
test.describe("release checks", () => {
  test.skip(({ isMobile }) => isMobile, "desktop only");

  test("sample end to end with privacy assertions", async ({ page, baseURL, context }) => {
    const origin = new URL(baseURL!).origin;
    const foreign: string[] = [];
    const errors: string[] = [];
    page.on("request", (r) => {
      const u = new URL(r.url());
      if (!["data:", "blob:"].includes(u.protocol) && u.origin !== origin) foreign.push(u.origin);
    });
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));

    // the manifest says whether genuine AI reviews are published for the sample
    const published = (await (await page.request.get("/sample/manifest.json")).json()).ai_bundle;
    await page.goto("/");
    await page.getByRole("button", { name: /Try the fictional sample/ }).click();
    await expect(page.getByRole("region", { name: "Recommendations" })).toBeVisible();

    if (published) {
      // AI-reviewed picks lead, labelled as genuine model output with the model's name
      const tabs = page.getByRole("tablist", { name: "Ranking" });
      await expect(tabs.getByRole("tab", { name: /AI-reviewed/ })).toHaveAttribute("aria-selected", "true");
      await expect(page.getByText(/Genuine reviews by/)).toContainText("openai/gpt-oss-120b");
      await expect(page.getByRole("main", { name: "Candidate detail" }).getByText("AI score")).toBeVisible();
      await tabs.getByRole("tab", { name: /Full show/ }).click();
    } else {
      await expect(page.getByText(/AI review not available/)).toBeVisible();
    }
    const rows = page.getByRole("option");
    await expect(rows.first()).toBeVisible();
    await rows.nth(2).click();
    const detail = page.getByRole("main", { name: "Candidate detail" });
    await expect(detail.getByRole("figure", { name: /Screenplay timeline/ })).toBeVisible();
    await expect(detail).toContainText(/\d\d:\d\d\.\d\d/); // exact MM:SS.cc timecodes
    const heading = await detail.getByRole("heading", { level: 2 }).textContent();
    await detail.getByRole("button", { name: /Accept/ }).click();
    await page.waitForTimeout(800);

    // deep-link reload restores from IndexedDB
    await page.reload();
    await expect(page.getByRole("main", { name: "Candidate detail" }).getByRole("heading", { level: 2 })).toHaveText(heading!);

    // export the analysis document, delete, and re-import it without uploading
    await page.getByRole("button", { name: "Export" }).click();
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("menuitem", { name: /Analysis document/ }).click(),
    ]);
    const json = Buffer.concat(await (await download.createReadStream()).toArray());
    expect(JSON.parse(json.toString("utf-8")).schema_version).toBe("framecue-analysis-v1");
    await page.getByRole("button", { name: "Analysis menu" }).click();
    await page.getByRole("menuitem", { name: /Delete analysis/ }).click();
    await page.getByRole("button", { name: /^Delete$/ }).click();
    await expect(page.getByRole("heading", { level: 1, name: /Find the moments worth promoting/ })).toBeVisible();

    let analyzeCalls = 0;
    page.on("request", (r) => r.url().endsWith("/api/analyze") && analyzeCalls++);
    await page.getByTestId("import-input").setInputFiles({ name: "analysis.json", mimeType: "application/json", buffer: json });
    await expect(page.getByRole("region", { name: "Recommendations" })).toBeVisible();
    expect(analyzeCalls).toBe(0);

    expect(await context.cookies()).toEqual([]);
    expect(await page.evaluate(() => localStorage.length)).toBe(0);
    expect([...new Set(foreign)]).toEqual([]);
    expect(errors).toEqual([]);
  });
});
