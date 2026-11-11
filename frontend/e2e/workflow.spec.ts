import { expect, test } from "@playwright/test";

// Uses only the public, fictional sample through the real API.
test.describe("desktop workflow", () => {
  test.skip(({ isMobile }) => isMobile, "desktop only");

  test("health, static assets and SPA deep links", async ({ page, request }) => {
    const health = await (await request.get("/api/health")).json();
    expect(health).toMatchObject({ status: "ok", offline: true, live_ai: false, stateless: true });
    const asset = await request.get("/sample/harbor-light-01.txt");
    expect(asset.status()).toBe(200);
    const missing = await request.get("/api/not-a-route");
    expect(missing.status()).toBe(404);
    expect((await missing.json()).error.code).toBe("not_found");
    await page.goto("/review/some/deep/link");
    await expect(page.getByRole("heading", { level: 1, name: /Find the moments worth promoting/ })).toBeVisible(); // nothing saved yet
  });

  test("sample analysis, review, refresh restore and deletion", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    await page.goto("/");
    await page.getByRole("button", { name: /Try the fictional sample/ }).click();
    await expect(page.getByRole("region", { name: "Recommendations" })).toBeVisible();
    await expect(page).toHaveURL(/\/review$/);
    await expect(page.getByRole("status")).toHaveText(/^\s*Saved$/);

    // when the sample has published AI reviews, use the full show for the CSV checks below
    const fullShow = page.getByRole("tab", { name: /Full show/ });
    if (await fullShow.count()) await fullShow.click();
    const rows = page.getByRole("option");
    await expect(rows.first()).toBeVisible();
    await rows.nth(1).click();
    const detail = page.getByRole("main", { name: "Candidate detail" });
    const heading = await detail.getByRole("heading", { level: 2 }).textContent();
    await detail.getByRole("button", { name: /Accept/ }).click();
    await detail.getByRole("button", { name: /Add note/ }).click();
    await detail.getByLabel("Private note").fill("Strong cold open");
    await page.waitForTimeout(800); // debounced saves

    await page.reload();
    await expect(page.getByRole("region", { name: "Recommendations" })).toBeVisible();
    const restored = page.getByRole("main", { name: "Candidate detail" });
    await expect(restored.getByRole("heading", { level: 2 })).toHaveText(heading!);
    await expect(restored.getByRole("button", { name: /Accept/ })).toHaveAttribute("aria-pressed", "true");
    await expect(restored.getByLabel("Private note")).toHaveValue("Strong cold open");

    // export downloads a CSV with the right header
    await page.getByRole("button", { name: "Export" }).click();
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("menuitem", { name: /Full show .* rows \(CSV\)/ }).click(),
    ]);
    const csv = await (await download.createReadStream()).toArray();
    const text = Buffer.concat(csv).toString("utf-8");
    expect(text.split("\n")[0]).toContain("view,rank,alternative_of,candidate_id,episode,start_timestamp");
    expect(text).toContain("heuristic_priority_v1");
    expect(text).toContain("accepted");

    // reopen from the saved-analysis list without analysing again
    let analyzeCalls = 0;
    page.on("request", (r) => r.url().endsWith("/api/analyze") && analyzeCalls++);
    await page.getByRole("button", { name: "Analysis menu" }).click();
    await page.getByRole("menuitem", { name: /New analysis/ }).click();
    const recent = page.getByRole("list", { name: "Recent analyses" });
    await expect(recent).toContainText("The Harbor Light (fictional sample)");
    await recent.getByText("The Harbor Light (fictional sample)").click();
    await expect(page.getByRole("main", { name: "Candidate detail" }).getByRole("heading", { level: 2 })).toHaveText(heading!);
    await expect(page.getByRole("main", { name: "Candidate detail" }).getByRole("button", { name: /Accept/ })).toHaveAttribute("aria-pressed", "true");
    expect(analyzeCalls).toBe(0);

    await page.getByRole("button", { name: "Analysis menu" }).click();
    await page.getByRole("menuitem", { name: /Delete analysis/ }).click();
    await page.getByRole("button", { name: /^Delete$/ }).click();
    await expect(page.getByRole("heading", { level: 1, name: /Find the moments worth promoting/ })).toBeVisible();
    await page.reload();
    await expect(page.getByRole("heading", { level: 1, name: /Find the moments worth promoting/ })).toBeVisible();
    expect(errors).toEqual([]);
  });

  test("client-side size limit stops oversized uploads before the network", async ({ page }) => {
    let analyzeCalls = 0;
    page.on("request", (r) => r.url().endsWith("/api/analyze") && analyzeCalls++);
    await page.goto("/");
    await page.getByTestId("screenplay-input").setInputFiles({
      name: "huge.zip",
      mimeType: "application/zip",
      buffer: Buffer.alloc(4_200_000),
    });
    await expect(page.getByRole("alert")).toContainText("online limit is 4.0 MB");
    await expect(page.getByRole("button", { name: /^Analyze/ })).toBeEnabled();
    await page.getByRole("button", { name: /^Analyze/ }).click();
    expect(analyzeCalls).toBe(0);
  });
});
