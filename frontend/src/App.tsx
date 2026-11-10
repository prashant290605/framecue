import { X } from "lucide-react";
import * as React from "react";
import { BrandMark } from "@/components/Brand";
import { UploadScreen } from "@/features/upload/UploadScreen";
import { cn } from "@/lib/utils";
import { AppProvider, useApp } from "@/state/app-state";

// The workspace (list, detail, timeline) loads only after an analysis exists.
const Workspace = React.lazy(() => import("@/features/workspace/Workspace").then((m) => ({ default: m.Workspace })));

export function App() {
  return (
    <AppProvider>
      <Shell />
    </AppProvider>
  );
}

function Shell() {
  const { state, dispatch } = useApp();
  const [atRoot, setAtRoot] = React.useState(() => window.location.pathname === "/");

  React.useEffect(() => {
    const onPop = () => setAtRoot(window.location.pathname === "/");
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  // Keep the address bar in step with what is shown (no data in the URL).
  React.useEffect(() => {
    if (state.phase === "workspace" && window.location.pathname === "/" && !atRoot) {
      history.replaceState(null, "", "/review");
    }
    if (state.phase === "upload" && window.location.pathname !== "/") {
      history.replaceState(null, "", "/");
      setAtRoot(true);
    }
  }, [state.phase, atRoot]);

  React.useEffect(() => {
    if (state.phase === "workspace") setAtRoot(window.location.pathname === "/");
  }, [state.phase, state.doc]);

  React.useEffect(() => {
    if (!state.notice) return;
    const t = setTimeout(() => dispatch({ type: "dismiss" }), 6000);
    return () => clearTimeout(t);
  }, [state.notice, dispatch]);

  let screen: React.ReactNode;
  if (state.phase === "boot") {
    screen = (
      <div className="flex h-dvh items-center justify-center" aria-busy="true" aria-label="Loading">
        <BrandMark className="size-8 animate-pulse" />
      </div>
    );
  } else if (state.phase === "workspace" && !atRoot) {
    screen = (
      <React.Suspense fallback={<div className="flex h-dvh items-center justify-center"><BrandMark className="size-8 animate-pulse" /></div>}>
        <Workspace />
      </React.Suspense>
    );
  } else {
    screen = <UploadScreen />;
  }

  return (
    <>
      {screen}
      <div aria-live="polite" className="pointer-events-none fixed inset-x-0 bottom-4 z-[60] flex justify-center px-4">
        {state.notice && (
          <div
            role={state.notice.tone === "error" ? "alert" : "status"}
            className={cn(
              "pointer-events-auto flex max-w-md items-start gap-3 rounded-lg border px-3.5 py-2.5 text-[13px] shadow-pop",
              state.notice.tone === "error" ? "border-no/25 bg-no-soft text-no" : "border-line bg-ink text-paper",
            )}
          >
            <span className="flex-1">{state.notice.text}</span>
            <button type="button" aria-label="Dismiss" onClick={() => dispatch({ type: "dismiss" })} className="opacity-70 hover:opacity-100">
              <X className="size-4" />
            </button>
          </div>
        )}
      </div>
    </>
  );
}
