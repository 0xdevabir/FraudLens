"use client";

import { useState } from "react";

import { Button, ErrorNote, inputClass } from "@/components/ui";
import { download } from "@/lib/api";
import { type Filters, type SavedView } from "@/lib/filters";

/** Saved filter sets for this browser: apply one with a click, save the current one under a name. */
export function SavedViewsBar({
  views, current, onApply, onSave, onRemove,
}: {
  views: SavedView[]; current: Filters; onApply: (filters: Filters) => void;
  onSave: (name: string, filters: Filters) => void; onRemove: (name: string) => void;
}) {
  const [name, setName] = useState("");
  const active = Object.values(current).some((values) => values.length);
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="mr-1 text-xs font-medium text-fg-3">Saved views</span>
      {views.map((view) => (
        <span key={view.name} className="inline-flex items-center overflow-hidden rounded-full bg-white/8 text-xs">
          <button type="button" className="px-3 py-1 font-medium text-fg-2 hover:bg-white/14" onClick={() => onApply(view.filters)}>
            {view.name}
          </button>
          <button
            type="button"
            aria-label={`Delete saved view ${view.name}`}
            className="px-2 py-1 text-fg-3 hover:bg-white/14 hover:text-fg"
            onClick={() => onRemove(view.name)}
          >
            ×
          </button>
        </span>
      ))}
      <form
        className="inline-flex items-center gap-1.5"
        onSubmit={(event) => {
          event.preventDefault();
          onSave(name, current);
          setName("");
        }}
      >
        <input
          aria-label="Name for this view"
          placeholder="Name this view"
          maxLength={40}
          value={name}
          onChange={(event) => setName(event.target.value)}
          className={`${inputClass} w-36 py-1 text-xs`}
        />
        <Button small type="submit" disabled={!name.trim() || !active}>Save</Button>
      </form>
    </div>
  );
}

/**
 * Download what the filters show as CSV. Identifiers are masked; a supervisor can ask for them,
 * and every export is written to the audit log.
 */
export function ExportButton({ path, name, canReveal }: { path: string; name: string; canReveal: boolean }) {
  const [busy, setBusy] = useState(false);
  const [reveal, setReveal] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  async function run() {
    setBusy(true);
    setError(null);
    try {
      await download(path + (reveal ? (path.includes("?") ? "&" : "?") + "reveal=true" : ""), `${name}.csv`);
    } catch (problem) {
      setError(problem as Error);
    } finally {
      setBusy(false);
    }
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      {canReveal && (
        <label className="flex items-center gap-1.5 text-xs text-fg-3">
          <input type="checkbox" checked={reveal} onChange={(event) => setReveal(event.target.checked)} />
          include identifiers
        </label>
      )}
      <Button small onClick={run} disabled={busy}>{busy ? "Preparing…" : "Export CSV"}</Button>
      {error && <ErrorNote error={error} />}
    </span>
  );
}
