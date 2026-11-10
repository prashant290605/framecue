import { FileJson, Layers, SearchX, Sparkles } from "lucide-react";
import { domAnimation, LazyMotion, MotionConfig } from "motion/react";
import * as React from "react";
import { Button } from "@/components/ui/button";
import { TooltipProvider } from "@/components/ui/overlays";
import { activeFilterCount, DEFAULT_FILTERS, filterEntries, type Decision } from "@/lib/doc-index";
import { useApp } from "@/state/app-state";
import { TopBar, Toolbar } from "./Chrome";
import { Detail } from "./Detail";
import { PromoList } from "./PromoList";

function useMediaQuery(query: string): boolean {
  const get = () => (typeof window !== "undefined" && window.matchMedia ? window.matchMedia(query).matches : true);
  const [matches, setMatches] = React.useState(get);
  React.useEffect(() => {
    if (!window.matchMedia) return;
    const m = window.matchMedia(query);
    const on = () => setMatches(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, [query]);
  return matches;
}

/** Motion and tooltip providers live here so the landing page does not load them. */
export function Workspace() {
  return (
    <LazyMotion features={domAnimation} strict>
      <MotionConfig reducedMotion="user">
        <TooltipProvider>
          <WorkspaceView />
        </TooltipProvider>
      </MotionConfig>
    </LazyMotion>
  );
}

function WorkspaceView() {
  const { state, dispatch } = useApp();
  const { index, view, filters, query, reviews, selectedId } = state;
  const desktop = useMediaQuery("(min-width: 1024px)");
  const [mobileDetail, setMobileDetail] = React.useState(false);
  const deferredQuery = React.useDeferredValue(query);

  const entries = React.useMemo(
    () => (index ? filterEntries(index, view, filters, deferredQuery, reviews) : []),
    [index, view, filters, deferredQuery, reviews],
  );

  const select = React.useCallback(
    (id: string) => {
      dispatch({ type: "select", id });
      setMobileDetail(true);
    },
    [dispatch],
  );

  // Keyboard decisions: A accept, L later, R reject (ignored while typing).
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (e.metaKey || e.ctrlKey || e.altKey || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName) || target.isContentEditable) return;
      if (!state.selectedId || document.querySelector("[role=dialog],[role=menu]")) return;
      const map: Record<string, Decision> = { a: "accepted", l: "later", r: "rejected" };
      const decision = map[e.key.toLowerCase()];
      if (!decision) return;
      const current = state.reviews[state.selectedId]?.decision ?? null;
      dispatch({ type: "decide", id: state.selectedId, decision: current === decision ? null : decision });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [state.selectedId, state.reviews, dispatch]);

  if (!index) return null;
  const aiEmpty = view === "ai" && index.entries.ai.length === 0;
  const filteredOut = !aiEmpty && entries.length === 0;

  const list = (
    <section aria-label="Recommendations" className="flex min-h-0 flex-col">
      <Toolbar shown={entries.length} />
      {view === "ai" && !aiEmpty && state.doc!.views.ai.provisional && (
        <p className="border-b border-line bg-amber-wash px-4 py-2 text-[12px] leading-snug text-amber-ink">
          Provisional: {state.doc!.views.ai.provisional_reason}
        </p>
      )}
      {view === "coverage" && (
        <p className="border-b border-line px-4 py-2 text-[12px] leading-snug text-muted">
          Unverified suggestions ranked by the heuristic baseline (0–1). Not AI quality scores.
        </p>
      )}
      <div className="min-h-0 flex-1 pt-2">
        {aiEmpty ? (
          <EmptyAi onFullShow={() => dispatch({ type: "view", view: "coverage" })} />
        ) : filteredOut ? (
          <EmptyFiltered
            canClear={activeFilterCount(filters) > 0 || query.length > 0}
            onClear={() => {
              dispatch({ type: "filters", filters: DEFAULT_FILTERS });
              dispatch({ type: "query", query: "" });
            }}
          />
        ) : (
          <PromoList index={index} view={view} entries={entries} reviews={reviews} selectedId={selectedId} onSelect={select} />
        )}
      </div>
    </section>
  );

  return (
    <div className="flex h-dvh flex-col">
      <TopBar entries={entries} />
      {desktop ? (
        <div className="grid min-h-0 flex-1 grid-cols-[minmax(360px,420px)_1fr]">
          <div className="flex min-h-0 flex-col border-r border-line bg-paper">{list}</div>
          <main className="min-h-0 overflow-y-auto bg-[#fcfbf9]" aria-label="Candidate detail">
            {aiEmpty ? <AiExplainer /> : <Detail />}
          </main>
        </div>
      ) : mobileDetail && selectedId && !aiEmpty ? (
        <main className="min-h-0 flex-1 overflow-y-auto" aria-label="Candidate detail">
          <Detail onBack={() => setMobileDetail(false)} />
        </main>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col">{list}</div>
      )}
    </div>
  );
}

function EmptyAi({ onFullShow }: { onFullShow: () => void }) {
  return (
    <div className="mx-4 mt-6 rounded-lg border border-line bg-surface p-5 text-center">
      <Sparkles className="mx-auto size-5 text-amber-ink" />
      <p className="mt-2 text-[14px] font-semibold">No AI-reviewed picks in this analysis</p>
      <p className="mx-auto mt-1 max-w-sm text-[13px] text-muted">
        AI judgements appear only when a validated evaluation bundle is uploaded with the
        screenplay. Nothing is estimated or generated in its place.
      </p>
      <Button variant="primary" className="mt-4" onClick={onFullShow}>
        <Layers /> Browse the full show
      </Button>
    </div>
  );
}

function AiExplainer() {
  return (
    <div className="mx-auto max-w-lg px-6 py-16 text-[13px] leading-relaxed text-muted">
      <h2 className="font-display text-[20px] font-semibold text-ink">How AI review works here</h2>
      <p className="mt-3">
        The deployed service never calls a model. AI-reviewed recommendations come from a
        private evaluation bundle produced locally with <code className="text-[12px]">promolens evaluate</code> and
        exported with <code className="text-[12px]">promolens export-ai</code>.
      </p>
      <p className="mt-3">
        When you upload that bundle with the same screenplay, each judgement is used only if it
        matches the screenplay exactly and its cited evidence re-validates. Everything else is
        ignored and counted in the coverage panel.
      </p>
      <p className="mt-3 flex items-center gap-2 text-ink-soft">
        <FileJson className="size-4 text-faint" /> Start a new analysis and add the bundle to see AI picks.
      </p>
    </div>
  );
}

function EmptyFiltered({ canClear, onClear }: { canClear: boolean; onClear: () => void }) {
  return (
    <div className="mx-4 mt-6 rounded-lg border border-dashed border-line-strong p-5 text-center">
      <SearchX className="mx-auto size-5 text-faint" />
      <p className="mt-2 text-[13.5px] font-medium">Nothing matches</p>
      <p className="mt-1 text-[12.5px] text-muted">Try fewer filters or a different search.</p>
      {canClear && (
        <Button size="sm" className="mt-3" onClick={onClear}>
          Clear search and filters
        </Button>
      )}
    </div>
  );
}
