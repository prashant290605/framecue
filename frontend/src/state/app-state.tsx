import * as React from "react";
import type { AnalysisDocument, ViewName } from "@/api/types";
import {
  buildIndex,
  DEFAULT_FILTERS,
  type Decision,
  type DocIndex,
  type Filters,
  type Reviews,
} from "@/lib/doc-index";
import {
  analysisKey,
  clearAll,
  loadAnalysis,
  saveAnalysis,
  saveReview,
  StorageError,
  type AnalysisMeta,
} from "@/lib/storage";

export type Origin = AnalysisMeta["origin"];
export type StorageStatus =
  | { state: "idle" }
  | { state: "saving" }
  | { state: "saved"; at: string }
  | { state: "failed"; message: string };

export interface Notice {
  id: number;
  tone: "info" | "error";
  text: string;
}

export interface State {
  phase: "boot" | "upload" | "workspace";
  doc: AnalysisDocument | null;
  index: DocIndex | null;
  fileName: string;
  origin: Origin;
  reviews: Reviews;
  view: ViewName;
  selectedId: string | null;
  filters: Filters;
  query: string;
  storage: StorageStatus;
  notice: Notice | null;
}

type Action =
  | { type: "booted" }
  | {
      type: "loaded";
      doc: AnalysisDocument;
      fileName: string;
      origin: Origin;
      reviews?: Reviews;
      view?: ViewName;
      selectedId?: string | null;
      filters?: Filters;
      restored?: boolean;
    }
  | { type: "view"; view: ViewName }
  | { type: "select"; id: string | null }
  | { type: "filters"; filters: Filters }
  | { type: "query"; query: string }
  | { type: "decide"; id: string; decision: Decision | null }
  | { type: "note"; id: string; note: string }
  | { type: "storage"; status: StorageStatus }
  | { type: "notice"; tone: Notice["tone"]; text: string }
  | { type: "dismiss" }
  | { type: "reset" };

export const initialState: State = {
  phase: "boot",
  doc: null,
  index: null,
  fileName: "",
  origin: "upload",
  reviews: {},
  view: "coverage",
  selectedId: null,
  filters: DEFAULT_FILTERS,
  query: "",
  storage: { state: "idle" },
  notice: null,
};

let noticeId = 0;

function firstOf(index: DocIndex, view: ViewName): string | null {
  return index.entries[view].find((e) => e.rank !== null)?.id ?? null;
}

export function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "booted":
      return { ...state, phase: state.doc ? "workspace" : "upload" };
    case "loaded": {
      const index = buildIndex(action.doc);
      const hasAi = Object.keys(action.doc.ai_evaluations).length > 0;
      const view = action.view ?? (hasAi ? "ai" : "coverage");
      const selected =
        action.selectedId && index.candidates.has(action.selectedId)
          ? action.selectedId
          : firstOf(index, view);
      return {
        ...state,
        phase: "workspace",
        doc: action.doc,
        index,
        fileName: action.fileName,
        origin: action.origin,
        reviews: action.reviews ?? {},
        view,
        selectedId: selected,
        filters: action.filters ?? DEFAULT_FILTERS,
        query: "",
        storage: action.restored ? state.storage : { state: "saving" },
      };
    }
    case "view": {
      if (!state.index) return state;
      const keep =
        state.selectedId && state.index.entryById[action.view].has(state.selectedId)
          ? state.selectedId
          : firstOf(state.index, action.view);
      return { ...state, view: action.view, selectedId: keep };
    }
    case "select":
      return { ...state, selectedId: action.id };
    case "filters":
      return { ...state, filters: action.filters };
    case "query":
      return { ...state, query: action.query };
    case "decide": {
      const prev = state.reviews[action.id];
      return {
        ...state,
        reviews: {
          ...state.reviews,
          [action.id]: { decision: action.decision, note: prev?.note ?? "", updatedAt: new Date().toISOString() },
        },
      };
    }
    case "note": {
      const prev = state.reviews[action.id];
      return {
        ...state,
        reviews: {
          ...state.reviews,
          [action.id]: { decision: prev?.decision ?? null, note: action.note, updatedAt: new Date().toISOString() },
        },
      };
    }
    case "storage":
      return { ...state, storage: action.status };
    case "notice":
      return { ...state, notice: { id: ++noticeId, tone: action.tone, text: action.text } };
    case "dismiss":
      return { ...state, notice: null };
    case "reset":
      return { ...initialState, phase: "upload", storage: { state: "idle" } };
  }
}

interface Ctx {
  state: State;
  dispatch: React.Dispatch<Action>;
  load: (doc: AnalysisDocument, fileName: string, origin: Origin) => void;
  deleteSaved: () => Promise<void>;
}

const AppContext = React.createContext<Ctx | null>(null);

export function useApp(): Ctx {
  const ctx = React.useContext(AppContext);
  if (!ctx) throw new Error("useApp outside AppProvider");
  return ctx;
}

const REVIEW_DEBOUNCE_MS = 300;

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [state, dispatch] = React.useReducer(reducer, initialState);
  const savedKey = React.useRef<string | null>(null);

  // Restore the last analysis on start (and after a refresh).
  React.useEffect(() => {
    let cancelled = false;
    loadAnalysis()
      .then((restored) => {
        if (cancelled) return;
        if (restored) {
          savedKey.current = restored.meta.key;
          dispatch({
            type: "loaded",
            doc: restored.doc,
            fileName: restored.meta.fileName,
            origin: restored.meta.origin,
            reviews: restored.reviews,
            view: restored.ui?.view,
            selectedId: restored.ui?.selectedId,
            filters: restored.ui?.filters ? { ...state.filters, ...restored.ui.filters } : undefined,
            restored: true,
          });
          dispatch({ type: "storage", status: { state: "saved", at: restored.meta.savedAt } });
        }
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const message = error instanceof StorageError ? error.message : "Saved data could not be read.";
        dispatch({ type: "storage", status: { state: "failed", message } });
        if (error instanceof StorageError && error.problem === "corrupt") {
          dispatch({ type: "notice", tone: "error", text: message });
        }
      })
      .finally(() => !cancelled && dispatch({ type: "booted" }));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const load = React.useCallback((doc: AnalysisDocument, fileName: string, origin: Origin) => {
    dispatch({ type: "loaded", doc, fileName, origin });
    // Save in the background; the workspace is usable immediately.
    savedKey.current = null;
    saveAnalysis(doc, { fileName, origin })
      .then((meta) => {
        savedKey.current = meta.key;
        dispatch({ type: "storage", status: { state: "saved", at: meta.savedAt } });
      })
      .catch((error: unknown) => {
        const message = error instanceof StorageError ? error.message : "Could not save locally.";
        dispatch({ type: "storage", status: { state: "failed", message } });
      });
  }, []);

  // Persist decisions, notes and position (debounced) once the analysis itself is saved.
  React.useEffect(() => {
    if (!state.doc || state.storage.state !== "saved") return;
    const key = analysisKey(state.doc);
    if (savedKey.current !== key) return;
    const timer = setTimeout(() => {
      saveReview(key, state.reviews, { view: state.view, selectedId: state.selectedId, filters: state.filters }).catch(
        (error: unknown) => {
          const message = error instanceof StorageError ? error.message : "Could not save your review.";
          dispatch({ type: "storage", status: { state: "failed", message } });
        },
      );
    }, REVIEW_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [state.doc, state.reviews, state.view, state.selectedId, state.filters, state.storage.state]);

  const deleteSaved = React.useCallback(async () => {
    try {
      await clearAll();
    } finally {
      savedKey.current = null;
      dispatch({ type: "reset" });
      dispatch({ type: "notice", tone: "info", text: "Saved analysis deleted from this browser." });
    }
  }, []);

  const value = React.useMemo(() => ({ state, dispatch, load, deleteSaved }), [state, load, deleteSaved]);
  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}
