import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ImageOff, RefreshCw, Trash2 } from "lucide-react";

import {
  getVideoDiagrams,
  regenerateDiagram,
  removeDiagram,
} from "../api/diagrams";
import type { DiagramAsset, DiagramMode } from "../types/diagram";

function DiagramEditor({ diagram, onChanged }: { diagram: DiagramAsset; onChanged: () => void }) {
  const [prompt, setPrompt] = useState(diagram.detailed_prompt ?? diagram.original_caption);
  const [mode, setMode] = useState<DiagramMode>(diagram.render_mode ?? "structured");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setPrompt(diagram.detailed_prompt ?? diagram.original_caption);
    setMode(diagram.render_mode ?? "structured");
  }, [diagram]);

  async function regenerate() {
    setBusy(true);
    try {
      await regenerateDiagram(diagram.id, { prompt, mode });
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await removeDiagram(diagram.id);
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="rounded-2xl border border-slate-200 bg-white p-5">
      <div className="flex flex-col gap-5 lg:flex-row">
        <div className="grid min-h-44 w-full place-items-center overflow-hidden rounded-xl bg-slate-100 lg:w-72">
          {diagram.image_url && diagram.state === "completed" ? (
            <img src={diagram.image_url} alt={diagram.alt_text ?? diagram.original_caption} className="max-h-64 w-full object-contain" />
          ) : (
            <div className="p-5 text-center text-sm text-slate-500">
              <ImageOff className="mx-auto mb-2 h-6 w-6" />
              {diagram.state.replace("_", " ")}
            </div>
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="font-semibold text-slate-900">{diagram.original_caption}</h3>
            <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-600">{diagram.state.replace("_", " ")}</span>
          </div>
          <label className="mt-4 block text-xs font-bold uppercase tracking-wide text-slate-500" htmlFor={`diagram-prompt-${diagram.id}`}>Generation prompt</label>
          <textarea
            id={`diagram-prompt-${diagram.id}`}
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            rows={4}
            className="mt-2 w-full rounded-xl border border-slate-200 p-3 text-sm"
          />
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <select value={mode} onChange={(event) => setMode(event.target.value as DiagramMode)} className="rounded-xl border border-slate-200 px-3 py-2 text-sm">
              <option value="structured">Structured Mermaid</option>
              <option value="illustrative">Illustrative image</option>
            </select>
            <button onClick={regenerate} disabled={busy || !prompt.trim()} className="secondary-button">
              <RefreshCw className="h-4 w-4" /> {busy ? "Working..." : diagram.state === "failed" ? "Retry" : "Regenerate"}
            </button>
            <button onClick={remove} disabled={busy} className="inline-flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-semibold text-rose-700 hover:bg-rose-50">
              <Trash2 className="h-4 w-4" /> Remove
            </button>
          </div>
          {diagram.error_message ? <p className="mt-3 text-sm text-rose-700">{diagram.error_message}</p> : null}
        </div>
      </div>
    </article>
  );
}

export function DiagramPanel({ videoId }: { videoId: string }) {
  const queryClient = useQueryClient();
  const diagrams = useQuery({
    queryKey: ["diagrams", videoId],
    queryFn: () => getVideoDiagrams(videoId),
    refetchInterval: (query) =>
      query.state.data?.some((item) => ["pending", "spec_generating", "rendering", "rate_limited"].includes(item.state))
        ? 4000
        : false,
  });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["diagrams", videoId] });
    void queryClient.invalidateQueries({ queryKey: ["notes", videoId] });
  };

  if (diagrams.isLoading) return <div className="h-32 animate-pulse rounded-2xl bg-slate-100" />;
  if (!diagrams.data?.length) return null;
  return (
    <section className="mb-7">
      <div className="mb-4">
        <p className="eyebrow">Visual enrichment</p>
        <h2 className="mt-1 text-xl font-bold">Lesson diagrams</h2>
      </div>
      <div className="space-y-4">
        {diagrams.data.map((diagram) => <DiagramEditor key={diagram.id} diagram={diagram} onChanged={refresh} />)}
      </div>
    </section>
  );
}
