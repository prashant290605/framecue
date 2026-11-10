import { CircleAlert, X } from "lucide-react";
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
    screen = <Splash />;
  } else if (state.phase === "workspace" && !atRoot) {
    screen = (
      <React.Suspense fallback={<Splash />}>
        <Workspace />
      </React.Suspense>
    );
  } else {
    screen = <UploadScreen />;
  }

  return (
    <>
      {screen}
      <div aria-live="polite" className="pointer-events-none fixed inset-x-0 bottom-5 z-[60] flex justify-center px-4">
        {state.notice && (
          <div
            role={state.notice.tone === "error" ? "alert" : "status"}
            className={cn(
              "pointer-events-auto flex max-w-md items-center gap-3 rounded-lg py-2.5 pr-2.5 pl-4 text-[13px] font-medium shadow-pop",
              state.notice.tone === "error" ? "border border-no/30 bg-surface text-no" : "bg-ink text-white",
            )}
          >
            {state.notice.tone === "error" && <CircleAlert className="size-4 shrink-0" aria-hidden />}
            <span className="flex-1">{state.notice.text}</span>
            <button type="button" aria-label="Dismiss" onClick={() => dispatch({ type: "dismiss" })} className="rounded-md p-1 opacity-70 hover:bg-white/10 hover:opacity-100">
              <X className="size-4" />
            </button>
          </div>
        )}
      </div>
    </>
  );
}

function Splash() {
  return (
    <div className="flex h-dvh items-center justify-center" aria-busy="true" aria-label="Loading">
      <BrandMark className="size-9 animate-pulse" />
    </div>
  );
}
