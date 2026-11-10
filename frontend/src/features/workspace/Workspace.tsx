import { FileJson, Info, Layers, SearchX } from "lucide-react";
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
    <section aria-label="Recommendations" className="flex min-h-0 flex-1 flex-col">
      <Toolbar shown={entries.length} />
      {view === "ai" && !aiEmpty && state.doc!.views.ai.provisional && (
        <ViewNote>
          <span>Provisional: {state.doc!.views.ai.provisional_reason}</span>
        </ViewNote>
      )}
      {view === "coverage" && (
        <ViewNote>
          <span>Unverified suggestions ranked by the heuristic baseline (0–1). Not AI quality scores.</span>
        </ViewNote>
      )}
      <div className="min-h-0 flex-1 pt-1.5">
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
    <div className="flex h-dvh flex-col bg-canvas">
      <TopBar entries={entries} />
      {desktop ? (
        <div className="grid min-h-0 flex-1 grid-cols-[392px_1fr] xl:grid-cols-[420px_1fr]">
          <div className="flex min-h-0 flex-col border-r border-line">{list}</div>
          <main className="min-h-0 overflow-y-auto bg-surface" aria-label="Candidate detail">
            {aiEmpty ? <AiExplainer /> : <Detail />}
          </main>
        </div>
      ) : mobileDetail && selectedId && !aiEmpty ? (
        <main className="min-h-0 flex-1 overflow-y-auto bg-surface" aria-label="Candidate detail">
          <Detail onBack={() => setMobileDetail(false)} />
        </main>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col">{list}</div>
      )}
    </div>
  );
}

function ViewNote({ children }: { children: React.ReactNode }) {
  return (
    <p className="flex gap-2 border-b border-line px-4 py-2.5 text-[12px] leading-snug text-muted">
      <Info className="mt-px size-3.5 shrink-0 text-faint" aria-hidden />
      {children}
    </p>
  );
}

function EmptyState({ icon, title, children }: { icon: React.ReactNode; title: string; children?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center px-8 pt-14 pb-8 text-center">
      <span className="flex size-10 items-center justify-center rounded-full bg-surface text-muted ring-1 ring-line [&_svg]:size-[18px]">{icon}</span>
      <p className="mt-4 text-[14px] font-semibold text-ink">{title}</p>
      {children}
    </div>
  );
}

function EmptyAi({ onFullShow }: { onFullShow: () => void }) {
  return (
    <EmptyState icon={<Layers />} title="No AI-reviewed picks in this analysis">
      <p className="mt-1.5 max-w-[300px] text-[13px] leading-relaxed text-muted">
        AI judgements appear only when a validated evaluation bundle is uploaded with the
        screenplay. Nothing is estimated or generated in its place.
      </p>
      <Button variant="primary" className="mt-5" onClick={onFullShow}>
        Browse the full show
      </Button>
    </EmptyState>
  );
}

function AiExplainer() {
  return (
    <div className="mx-auto max-w-[480px] px-8 py-20">
      <span className="flex size-10 items-center justify-center rounded-full bg-accent-soft text-accent">
        <FileJson className="size-[18px]" />
      </span>
      <h2 className="mt-5 text-[20px] font-bold tracking-[-0.02em] text-ink">How AI review works here</h2>
      <div className="mt-3 space-y-3 text-[14px] leading-relaxed text-muted">
        <p>
          The deployed service never calls a model. AI-reviewed recommendations come from a private
          evaluation bundle produced locally with the command-line tool, using{" "}
          <code className="font-mono text-[12px] whitespace-nowrap text-ink-soft">promolens evaluate</code> and then{" "}
          <code className="font-mono text-[12px] whitespace-nowrap text-ink-soft">promolens export-ai</code>.
        </p>
        <p>
          Upload that bundle with the same screenplay and each judgement is used only if it matches
          the screenplay exactly and its cited evidence re-validates. Everything else is ignored and
          counted in the coverage panel.
        </p>
      </div>
    </div>
  );
}

function EmptyFiltered({ canClear, onClear }: { canClear: boolean; onClear: () => void }) {
  return (
    <EmptyState icon={<SearchX />} title="Nothing matches">
      <p className="mt-1.5 text-[13px] text-muted">Try fewer filters or a different search.</p>
      {canClear && (
        <Button size="sm" className="mt-4" onClick={onClear}>
          Clear search and filters
        </Button>
      )}
    </EmptyState>
  );
}
