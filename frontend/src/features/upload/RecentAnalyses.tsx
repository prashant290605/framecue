import { Check, Download, Pencil, Trash2, X } from "lucide-react";
import * as React from "react";
import { Button } from "@/components/ui/button";
import { download } from "@/lib/exporters";
import { exportBackup, type AnalysisMeta } from "@/lib/storage";
import { cn } from "@/lib/utils";
import { useApp } from "@/state/app-state";

const when = (iso?: string) =>
  iso ? new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "";

/** Saved analyses in this browser: open, rename, delete, and back up all of them. */
export function RecentAnalyses({ items, onChange }: { items: AnalysisMeta[]; onChange: () => void }) {
  const { dispatch } = useApp();
  if (items.length === 0) return null;
  const backup = async () => {
    try {
      const data = await exportBackup();
      download(`framecue-backup-${new Date().toISOString().slice(0, 10)}.json`, JSON.stringify(data), "application/json");
    } catch {
      dispatch({ type: "notice", tone: "error", text: "The backup could not be created." });
    }
  };
  return (
    <section aria-labelledby="recent-heading" className="mt-10">
      <div className="flex items-center justify-between gap-4">
        <h2 id="recent-heading" className="text-[13px] font-semibold text-ink">
          Recent analyses
        </h2>
        <Button size="sm" variant="ghost" className="-mr-2 text-muted" onClick={() => void backup()}>
          <Download /> Back up all
        </Button>
      </div>
      <ul className="mt-2 divide-y divide-line rounded-xl border border-line bg-surface" aria-label="Recent analyses">
        {items.map((m) => (
          <Item key={m.key} meta={m} onChange={onChange} />
        ))}
      </ul>
    </section>
  );
}

function Item({ meta, onChange }: { meta: AnalysisMeta; onChange: () => void }) {
  const { open, rename, remove } = useApp();
  const [mode, setMode] = React.useState<"idle" | "rename" | "delete">("idle");
  const [name, setName] = React.useState(meta.fileName);
  const facts = [
    meta.episodes != null && `${meta.episodes} episodes`,
    meta.candidates != null && `${meta.candidates} candidates`,
    meta.aiReviewed ? `${meta.aiReviewed} AI-reviewed` : null,
    when(meta.openedAt ?? meta.savedAt),
  ].filter(Boolean);

  if (mode === "rename") {
    return (
      <li className="px-3 py-2.5">
        <form
          className="flex items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void rename(meta.key, name).then(onChange);
            setMode("idle");
          }}
        >
          <input
            aria-label="Analysis name"
            value={name}
            maxLength={120}
            autoFocus
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Escape" && setMode("idle")}
            className="h-8 min-w-0 flex-1 rounded-md border border-line-strong bg-surface px-2.5 text-[13px] text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/20"
          />
          <Button size="icon-sm" type="submit" variant="ghost" aria-label="Save name" disabled={!name.trim()}>
            <Check />
          </Button>
          <Button size="icon-sm" type="button" variant="ghost" aria-label="Cancel rename" onClick={() => setMode("idle")}>
            <X />
          </Button>
        </form>
      </li>
    );
  }

  return (
    <li className="group flex items-center gap-2 pr-2">
      <button
        type="button"
        onClick={() => {
          history.pushState(null, "", "/review");
          void open(meta.key);
        }}
        className="min-w-0 flex-1 rounded-l-xl px-4 py-3 text-left transition-colors hover:bg-canvas"
      >
        <span className="block truncate text-[13px] font-semibold text-ink">{meta.fileName}</span>
        <span className="mt-0.5 block truncate text-[12px] text-muted">{facts.join(" · ")}</span>
      </button>
      {mode === "delete" ? (
        <span className="flex shrink-0 items-center gap-1.5">
          <span className="text-[12px] font-medium text-muted max-sm:hidden">Delete?</span>
          <Button
            size="sm"
            variant="danger"
            onClick={() => void remove(meta.key).then(onChange)}
            aria-label={`Confirm delete ${meta.fileName}`}
          >
            Delete
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setMode("idle")}>
            Cancel
          </Button>
        </span>
      ) : (
        <span className={cn("flex shrink-0 items-center gap-0.5 transition-opacity sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100")}>
          <Button size="icon-sm" variant="ghost" aria-label={`Rename ${meta.fileName}`} onClick={() => setMode("rename")}>
            <Pencil />
          </Button>
          <Button size="icon-sm" variant="ghost" aria-label={`Delete ${meta.fileName}`} onClick={() => setMode("delete")}>
            <Trash2 />
          </Button>
        </span>
      )}
    </li>
  );
}
