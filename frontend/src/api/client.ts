import type { AnalysisDocument } from "./types";
import { validateDocument } from "@/lib/validate";

/* The frontend uses only the stateless contract: GET /api/health and POST /api/analyze.
 * No keys, no analytics, no third-party calls. */

export const DEFAULT_MAX_UPLOAD = 4_000_000;
export const REQUEST_TIMEOUT_MS = 90_000;

export interface Health {
  status: string;
  version: string;
  offline: boolean;
  live_ai: boolean;
  stateless?: boolean;
  max_upload_bytes?: number;
}

export class ApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status?: number,
  ) {
    super(message);
  }
}

const FRIENDLY: Record<string, string> = {
  upload_too_large: "This file is larger than the online limit.",
  too_many_episodes: "This pack has more episodes than the online limit.",
  analysis_too_large: "The analysis is too large to return online.",
  unsupported_file_type: "Only .zip and .txt screenplay files are supported.",
  mixed_inputs: "Upload either one .zip file or one or more .txt files.",
  not_a_zip: "That .zip file is not a valid ZIP archive.",
  no_valid_episodes: "No valid screenplay episode was found in the upload.",
  invalid_ai_bundle: "The AI evaluation bundle could not be read.",
  no_files: "Choose a screenplay file first.",
  too_many_files: "Too many files in one upload.",
};

export const CLI_HINT =
  "For larger inputs, run the local command line tool: promolens analyze <file> --out <folder>.";

export async function getHealth(signal?: AbortSignal): Promise<Health> {
  const response = await fetch("/api/health", { signal, headers: { Accept: "application/json" } });
  if (!response.ok) throw new ApiError("health_failed", "The analysis service is not responding.", response.status);
  return (await response.json()) as Health;
}

export interface AnalyzeInput {
  files: File[];
  bundle?: File | null;
  signal?: AbortSignal;
  timeoutMs?: number;
}

export async function analyze({ files, bundle, signal, timeoutMs = REQUEST_TIMEOUT_MS }: AnalyzeInput): Promise<AnalysisDocument> {
  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);
  if (bundle) form.append("ai_bundle", bundle, bundle.name);

  const controller = new AbortController();
  const onAbort = () => controller.abort(signal?.reason);
  signal?.addEventListener("abort", onAbort, { once: true });
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  let response: Response;
  try {
    response = await fetch("/api/analyze", {
      method: "POST",
      body: form,
      signal: controller.signal,
      headers: { Accept: "application/json" },
    });
  } catch (error) {
    if (timedOut) throw new ApiError("timeout", "The analysis took too long and was stopped.");
    if (controller.signal.aborted) throw new ApiError("cancelled", "Analysis cancelled.");
    throw new ApiError("network", "Could not reach the analysis service. Check your connection and try again.");
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onAbort);
  }

  let body: unknown;
  const text = await response.text();
  try {
    body = JSON.parse(text);
  } catch {
    body = undefined;
  }
  if (!response.ok) {
    const err = (body as { error?: { code?: string; message?: string } } | undefined)?.error;
    if (response.status === 413) {
      throw new ApiError(err?.code ?? "upload_too_large", `${FRIENDLY.upload_too_large} ${CLI_HINT}`, 413);
    }
    const code = err?.code ?? (response.status >= 500 ? "server_error" : "request_failed");
    const message =
      FRIENDLY[code] ??
      (response.status >= 500
        ? "The analysis service had a problem. Please try again in a moment."
        : (err?.message ?? "The request was rejected."));
    throw new ApiError(code, message, response.status);
  }
  const check = validateDocument(body);
  if (!check.ok) {
    throw new ApiError("malformed_response", `The service returned an unexpected response (${check.reason}).`, response.status);
  }
  return check.doc;
}
