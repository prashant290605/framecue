import type { Lane } from "./doc-index";

export const WARNING_LABELS: Record<string, string> = {
  no_clear_hook: "No clear hook",
  hook_too_late: "Hook lands late",
  weak_ending: "Weak ending",
  resolved_before_cut: "Resolved before the cut",
  needs_prior_context: "Needs prior context",
  unclear_relationships: "Unclear relationships",
  spoiler_inside_clip: "Spoiler inside clip",
  dead_air: "Dead air",
  starts_mid_action: "Starts mid-action",
  ends_mid_action: "Ends mid-action",
};

export const warningLabel = (code: string) => WARNING_LABELS[code] ?? code.replace(/_/g, " ");

export const LANE_LABELS: Record<Lane, string> = {
  DIALOGUE: "Dialogue",
  ACTION: "Action",
  SOUND_FX: "Sound",
  CAMERA: "Camera",
};

const ANCHORS: Record<string, string> = {
  episode_start: "episode start",
  episode_end: "episode end",
  scene_start: "scene start",
  scene_end: "scene end",
  camera_cut: "camera cut",
  action_end: "end of an action",
  line_end: "end of a line",
  open_line_end: "end of an open line",
  before_line: "just before a line",
  before_answer: "just before an answer",
  shot_end_after_line: "shot end after a line",
  shot_end_after_open_line: "shot end after an open line",
  dialogue_onset: "start of a line",
  action_onset: "start of an action",
  sound_onset: "start of a sound",
  sound_end: "end of a sound",
  fallback_grid: "regular grid point",
};

/** "cue:question" -> "question cue"; codes are categories, never script text. */
export const anchorLabel = (code: string) =>
  code.startsWith("cue:") ? `${code.slice(4).replace(/_/g, " ")} cue` : (ANCHORS[code] ?? code.replace(/_/g, " "));

export const COMPONENT_LABELS: Record<string, string> = {
  opening: "Opening signal",
  ending: "Ending signal",
  activity: "Activity",
  boundary: "Clean boundaries",
  duration: "Duration fit",
  context: "Context",
};

export const RUBRIC = [
  { key: "hook_score", label: "Hook", hint: "First 3 seconds" },
  { key: "cliffhanger_score", label: "Cliffhanger", hint: "Final 5 seconds and the cut" },
  { key: "standalone_score", label: "Standalone", hint: "Clear to a first-time viewer" },
  { key: "pacing_score", label: "Pacing", hint: "Momentum across the clip" },
] as const;

export const RUBRIC_WEIGHT_KEY: Record<(typeof RUBRIC)[number]["key"], string> = {
  hook_score: "hook",
  cliffhanger_score: "cliffhanger",
  standalone_score: "standalone",
  pacing_score: "pacing",
};
