import { expect, test } from "@playwright/test";

test("mobile: stacked list and detail navigation", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: /Find the moments worth promoting/ })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0); // no horizontal scrolling
  await page.getByRole("button", { name: /Try the fictional sample/ }).click();
  await expect(page.getByRole("region", { name: "Recommendations" })).toBeVisible();
  await page.getByRole("option").first().click();
  const detail = page.getByRole("main", { name: "Candidate detail" });
  await expect(detail.getByRole("heading", { level: 2 })).toBeVisible();
  await expect(detail.getByRole("figure", { name: /Screenplay timeline/ })).toBeVisible();
  const detailOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(detailOverflow).toBeLessThanOrEqual(0);
  await detail.getByRole("button", { name: "All candidates" }).click();
  await expect(page.getByRole("option").first()).toBeVisible();
});
