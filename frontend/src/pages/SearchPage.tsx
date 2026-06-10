import { useState, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { ExternalLink, Search } from "lucide-react";

import { searchNotes } from "../api/search";
import { EmptyState } from "../components/EmptyState";

export function SearchPage() {
  const [query, setQuery] = useState("");
  const mutation = useMutation({ mutationFn: () => searchNotes({ query: query.trim(), top_k: 10 }) });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (query.trim()) mutation.mutate();
  }

  return (
    <div>
      <p className="eyebrow">Semantic search</p>
      <h1 className="page-title">Search your knowledge</h1>
      <form onSubmit={submit} className="mt-7 flex max-w-3xl gap-2 rounded-2xl border border-slate-200 bg-white p-2 shadow-sm">
        <Search className="ml-3 mt-3 h-5 w-5 text-slate-400" />
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Ask about any concept across your courses..." className="min-w-0 flex-1 px-2 outline-none" />
        <button className="rounded-xl bg-slate-950 px-5 py-3 text-sm font-semibold text-white">Search</button>
      </form>
      <div className="mt-8 space-y-4">
        {mutation.data?.map((result) => (
          <article key={result.chunk_id} className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div><p className="text-xs font-bold uppercase tracking-wide text-blue-600">{result.section_heading}</p><h2 className="mt-1 font-bold">{result.video_title}</h2></div>
              <span className="rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-700">{Math.round(result.similarity_score * 100)}% match</span>
            </div>
            <p className="mt-4 leading-7 text-slate-600">{result.text}</p>
            <a href={result.timestamp_url} target="_blank" rel="noreferrer" className="mt-4 inline-flex items-center gap-1 text-sm font-semibold text-blue-600">Watch at timestamp <ExternalLink className="h-3.5 w-3.5" /></a>
          </article>
        ))}
        {mutation.data?.length === 0 ? <EmptyState title="No matching notes" description="Try a broader phrase or a related concept." /> : null}
      </div>
    </div>
  );
}
