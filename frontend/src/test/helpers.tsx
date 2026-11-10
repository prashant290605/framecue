import { vi } from "vitest";
import type { AnalysisDocument } from "@/api/types";
import plain from "./fixtures/sample-document.json";
import withAi from "./fixtures/sample-document-ai.json";

/* Fixtures are real API output for the FICTIONAL "Harbor Light" sample. The AI fixture's
 * judgements come from a mocked model (canned test text), used only in tests. */
export const sampleDoc = () => structuredClone(plain) as unknown as AnalysisDocument;
export const sampleDocWithAi = () => structuredClone(withAi) as unknown as AnalysisDocument;

export interface FetchPlan {
  analyze?: (init: RequestInit) => Response | Promise<Response>;
  health?: () => Response;
}

export const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

/** Stub global fetch. Unplanned URLs fail the test loudly; nothing reaches the network. */
export function mockFetch(plan: FetchPlan = {}) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    calls.push({ url, init });
    if (url.endsWith("/api/health")) {
      return plan.health?.() ?? json({ status: "ok", version: "test", offline: true, live_ai: false, stateless: true, max_upload_bytes: 4_000_000 });
    }
    if (url.endsWith("/api/analyze")) {
      if (!plan.analyze) throw new Error("unexpected analyze call");
      return plan.analyze(init ?? {});
    }
    if (url.startsWith("/sample/")) return new Response("EPISODE NUMBER: Episode 1\n", { status: 200 });
    throw new Error(`unexpected fetch ${url}`);
  });
  vi.stubGlobal("fetch", fn);
  return { fn, calls };
}

export const file = (name: string, content = "x", type = "text/plain") => new File([content], name, { type });
