import { describe, expect, it } from "vitest";
import { checkSelection } from "@/features/upload/UploadScreen";
import { file, json, mockFetch, sampleDoc } from "@/test/helpers";
import { analyze, ApiError } from "./client";

const run = (extra: Partial<Parameters<typeof analyze>[0]> = {}) =>
  analyze({ files: [file("pack.zip", "PK")], ...extra });

describe("analyze()", () => {
  it("posts multipart files and an optional bundle, and validates the document", async () => {
    const { calls } = mockFetch({ analyze: () => json(sampleDoc()) });
    const doc = await run({ bundle: file("ai.json", "{}", "application/json") });
    expect(doc.summary.candidates).toBe(doc.candidates.length);
    const body = calls[0].init?.body as FormData;
    expect(calls[0].url).toBe("/api/analyze");
    expect(calls[0].init?.method).toBe("POST");
    expect(body.getAll("files")).toHaveLength(1);
    expect((body.get("ai_bundle") as File).name).toBe("ai.json");
  });

  it.each([
    [413, "platform text, not JSON", "upload_too_large", /online limit.*promolens analyze/s],
    [400, { error: { code: "invalid_ai_bundle", message: "x" } }, "invalid_ai_bundle", /AI evaluation bundle/],
    [422, { error: { code: "no_valid_episodes", message: "x" } }, "no_valid_episodes", /No valid screenplay/],
    [500, { error: { code: "internal_error", message: "x" } }, "internal_error", /had a problem/],
    [502, "<html>bad gateway</html>", "server_error", /had a problem/],
  ])("maps HTTP %s to a clear error", async (status, body, code, message) => {
    mockFetch({
      analyze: () =>
        typeof body === "string" ? new Response(body, { status }) : json(body, status),
    });
    const error = await run().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBe(code);
    expect((error as ApiError).message).toMatch(message);
  });

  it("reports network failures", async () => {
    mockFetch({
      analyze: () => {
        throw new TypeError("Failed to fetch");
      },
    });
    await expect(run()).rejects.toMatchObject({ code: "network" });
  });

  it("rejects malformed or mismatched responses", async () => {
    mockFetch({ analyze: () => json({ hello: "world" }) });
    await expect(run()).rejects.toMatchObject({ code: "malformed_response" });
    const doc = sampleDoc() as any;
    doc.views.coverage.order.push("C999-0000000-0000001");
    mockFetch({ analyze: () => json(doc) });
    await expect(run()).rejects.toMatchObject({ code: "malformed_response" });
  });

  it("supports cancellation and a timeout", async () => {
    const slow = (init: RequestInit) =>
      new Promise<Response>((_, reject) =>
        init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))),
      );
    mockFetch({ analyze: slow });
    const controller = new AbortController();
    const pending = run({ signal: controller.signal });
    controller.abort();
    await expect(pending).rejects.toMatchObject({ code: "cancelled" });
    mockFetch({ analyze: slow });
    await expect(run({ timeoutMs: 20 })).rejects.toMatchObject({ code: "timeout" });
  });
});

describe("checkSelection() before any upload", () => {
  const max = 4_000_000;
  it("accepts one zip, or txt files, with an optional json bundle", () => {
    expect(checkSelection([file("pack.zip")], null, max)).toBeNull();
    expect(checkSelection([file("a.txt"), file("b.txt")], file("ai.json"), max)).toBeNull();
  });
  it("rejects unsupported, mixed, missing and oversized selections", () => {
    expect(checkSelection([], null, max)?.title).toMatch(/Choose a screenplay/);
    expect(checkSelection([file("script.pdf")], null, max)?.title).toMatch(/not a supported file/);
    expect(checkSelection([file("a.zip"), file("b.txt")], null, max)?.title).toMatch(/not both/);
    expect(checkSelection([file("a.txt")], file("bundle.txt"), max)?.title).toMatch(/must be a .json/);
    const big = new File([new Uint8Array(4_100_000)], "big.zip");
    const problem = checkSelection([big], null, max);
    expect(problem?.title).toMatch(/online limit is 4.0 MB/);
    expect(problem?.detail).toMatch(/promolens analyze/);
  });
});
