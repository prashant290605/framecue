// Friendly aliases over the types generated from the backend's OpenAPI schema
// (`npm run gen:types` regenerates schema.d.ts from the stateless FastAPI app).
import type { components } from "./schema";

type S = components["schemas"];

export type AnalysisDocument = S["AnalysisDocument"];
export type DocCandidate = S["DocCandidate"];
export type DocEpisode = S["DocEpisode"];
export type DocEvent = S["DocEvent"];
export type DocView = S["DocView"];
export type AIJudgement = S["AIJudgement"];
export type EvaluationCoverage = S["EvaluationCoverage"];
export type Segment = S["Segment"];

export type ViewName = "ai" | "coverage";
export type EvaluationStatus = DocCandidate["evaluation_status"];

export const SCHEMA_VERSION = "promolens-analysis-v1" as const;
