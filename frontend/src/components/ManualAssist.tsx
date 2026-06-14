import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Clipboard, Loader2, X } from "lucide-react";

import { getManualPrompt, submitManualNotes } from "../api/notes";

export function ManualAssist({
  videoId,
  onClose,
}: {
  videoId: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [chunkIndex, setChunkIndex] = useState(0);
  const [response, setResponse] = useState("");
  const [copied, setCopied] = useState(false);
  const prompt = useQuery({
    queryKey: ["manual-prompt", videoId, chunkIndex],
    queryFn: () => getManualPrompt(videoId, chunkIndex),
  });
  const submit = useMutation({
    mutationFn: () => submitManualNotes(videoId, chunkIndex, response.trim()),
    onSuccess(result) {
      if (result.status === "complete") {
        void queryClient.invalidateQueries({ queryKey: ["video", videoId] });
        void queryClient.invalidateQueries({ queryKey: ["notes", videoId] });
        onClose();
        return;
      }
      const nextChunk = Array.from(
        { length: result.total_chunks },
        (_, index) => index,
      ).find((index) => !result.received_chunks.includes(index));
      if (nextChunk !== undefined) {
        setChunkIndex(nextChunk);
        setResponse("");
      }
    },
  });

  async function copyPrompt() {
    if (!prompt.data) return;
    await navigator.clipboard.writeText(prompt.data.prompt_text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div className="fixed inset-0 z-[80] overflow-y-auto bg-slate-950/70 p-4 backdrop-blur-sm">
      <div className="mx-auto my-6 max-w-4xl rounded-3xl bg-white p-6 shadow-2xl sm:p-8">
        <header className="flex items-start justify-between gap-4">
          <div>
            <p className="eyebrow">Manual assist</p>
            <h2 className="mt-2 text-2xl font-bold">Use any LLM for higher-quality notes</h2>
            <p className="mt-2 text-sm leading-6 text-slate-500">
              Copy the prepared prompt, paste it into your preferred model, then paste its Markdown response below.
            </p>
          </div>
          <button onClick={onClose} className="rounded-xl bg-slate-100 p-2" aria-label="Close manual assist">
            <X />
          </button>
        </header>

        {prompt.isLoading ? (
          <div className="grid h-64 place-items-center"><Loader2 className="animate-spin text-blue-600" /></div>
        ) : prompt.data ? (
          <>
            <div className="mt-7 flex flex-wrap items-center justify-between gap-3">
              <p className="text-sm font-semibold text-slate-600">
                Chunk {prompt.data.chunk_index + 1} of {prompt.data.total_chunks}
                <span className="ml-2 font-normal text-slate-400">
                  about {prompt.data.estimated_tokens.toLocaleString()} tokens
                </span>
              </p>
              <button onClick={copyPrompt} className="secondary-button">
                {copied ? <Check className="h-4 w-4" /> : <Clipboard className="h-4 w-4" />}
                {copied ? "Copied" : "Copy prompt"}
              </button>
            </div>
            <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap rounded-2xl bg-slate-950 p-5 text-xs leading-6 text-slate-200">
              {prompt.data.prompt_text}
            </pre>
            <label htmlFor="manual-response" className="mt-7 block text-sm font-semibold">
              LLM Markdown response
            </label>
            <textarea
              id="manual-response"
              value={response}
              onChange={(event) => setResponse(event.target.value)}
              rows={12}
              placeholder="Paste the response containing ## headings here..."
              className="mt-2 w-full rounded-2xl border border-slate-200 p-4 font-mono text-sm outline-none focus:border-blue-500"
            />
            {submit.error ? <p className="mt-3 text-sm text-rose-600">{submit.error.message}</p> : null}
            <button
              onClick={() => submit.mutate()}
              disabled={!response.trim() || submit.isPending}
              className="primary-button mt-5"
            >
              {submit.isPending ? "Validating..." : prompt.data.total_chunks > 1 ? "Save chunk" : "Save notes"}
            </button>
          </>
        ) : (
          <p className="mt-8 text-rose-600">
            {prompt.error?.message ?? "Could not prepare the manual prompt."}
          </p>
        )}
      </div>
    </div>
  );
}
