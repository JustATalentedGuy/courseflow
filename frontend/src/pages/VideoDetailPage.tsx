import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Clipboard, Download, FileDown, FileText, HelpCircle, RefreshCw, Sparkles, WandSparkles } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import { getVideoNotes, regenerateVideoNotes } from "../api/notes";
import { downloadFile } from "../api/client";
import { getVideo, getVideoTranscript, retryVideo } from "../api/videos";
import { ManualAssist } from "../components/ManualAssist";
import { DiagramPanel } from "../components/DiagramPanel";
import { MarkdownViewer } from "../components/MarkdownViewer";
import { QuizFocus } from "../components/QuizFocus";

export function VideoDetailPage() {
  const { id = "" } = useParams();
  const [tab, setTab] = useState<"notes" | "transcript">("notes");
  const [quizOpen, setQuizOpen] = useState(false);
  const [manualOpen, setManualOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [downloading, setDownloading] = useState<"markdown" | "pdf" | null>(null);
  const [generationAction, setGenerationAction] = useState<"retry" | "standard" | "high" | null>(null);
  const [actionError, setActionError] = useState("");
  const queryClient = useQueryClient();
  const video = useQuery({
    queryKey: ["video", id],
    queryFn: () => getVideo(id, ""),
    enabled: Boolean(id),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && ["pending", "processing", "rate_limited", "batch_processing", "waiting_for_transcript", "transcribing"].includes(status)
        ? 3000
        : false;
    },
  });
  const notes = useQuery({ queryKey: ["notes", id], queryFn: () => getVideoNotes(id, ""), enabled: Boolean(id) && video.data?.status === "completed" });
  const transcript = useQuery({ queryKey: ["transcript", id], queryFn: () => getVideoTranscript(id, ""), enabled: Boolean(id) && tab === "transcript" && video.data?.status === "completed" });

  async function copyMarkdown() {
    if (!notes.data) return;
    await navigator.clipboard.writeText(notes.data.full_markdown);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }

  async function exportNotes(format: "markdown" | "pdf") {
    setDownloading(format);
    try {
      const extension = format === "markdown" ? "md" : "pdf";
      await downloadFile(`/videos/${id}/export/${format}`, `courseflow-notes.${extension}`);
    } finally {
      setDownloading(null);
    }
  }

  async function regenerate(quality: "standard" | "high") {
    setGenerationAction(quality);
    setActionError("");
    try {
      const regenerated = await regenerateVideoNotes(id, "", quality);
      queryClient.setQueryData(["notes", id], regenerated);
      await video.refetch();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Could not regenerate notes.");
    } finally {
      setGenerationAction(null);
    }
  }

  async function retryProcessing() {
    setGenerationAction("retry");
    setActionError("");
    try {
      const updated = await retryVideo(id, "");
      queryClient.setQueryData(["video", id], updated);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Could not retry this video.");
    } finally {
      setGenerationAction(null);
    }
  }

  if (video.isLoading) return <div className="h-72 animate-pulse rounded-3xl bg-slate-200" />;
  if (!video.data) return <p className="text-rose-600">Video not found.</p>;

  return (
    <div>
      <Link to={`/courses/${video.data.course_id}`} className="text-sm font-semibold text-slate-500 hover:text-slate-900">Back to course</Link>
      <div className="mt-5 flex flex-col justify-between gap-5 lg:flex-row lg:items-end">
        <div>
          <p className="eyebrow">Lesson {video.data.position}</p>
          <h1 className="mt-2 max-w-4xl text-3xl font-bold">{video.data.title}</h1>
        </div>
        <div className="flex flex-wrap gap-2">
          <button onClick={() => setQuizOpen(true)} disabled={video.data.status !== "completed"} className="primary-button"><HelpCircle className="h-4 w-4" /> Start quiz</button>
          <button onClick={copyMarkdown} disabled={!notes.data} className="secondary-button"><Clipboard className="h-4 w-4" /> {copied ? "Copied" : "Copy Markdown"}</button>
          <button onClick={() => exportNotes("markdown")} disabled={!notes.data || downloading !== null} className="secondary-button"><FileDown className="h-4 w-4" /> Export Markdown</button>
          <button onClick={() => exportNotes("pdf")} disabled={!notes.data || downloading !== null} className="secondary-button"><Download className="h-4 w-4" /> Export PDF</button>
          <button onClick={() => regenerate("standard")} disabled={!notes.data || generationAction !== null} className="secondary-button"><RefreshCw className="h-4 w-4" /> {generationAction === "standard" ? "Regenerating..." : "Regenerate with Scout"}</button>
          <button onClick={() => regenerate("high")} disabled={!notes.data || generationAction !== null} className="secondary-button"><Sparkles className="h-4 w-4" /> {generationAction === "high" ? "Generating with 70B..." : "High quality (70B)"}</button>
          <button onClick={() => setManualOpen(true)} className="secondary-button"><WandSparkles className="h-4 w-4" /> Generate manually</button>
        </div>
      </div>
      {actionError ? <p className="mt-4 rounded-xl bg-rose-50 px-4 py-3 text-sm text-rose-700">{actionError}</p> : null}
      <div className="mt-8 flex gap-2 border-b border-slate-200">
        <button onClick={() => setTab("notes")} className={`tab-button ${tab === "notes" ? "tab-active" : ""}`}><FileText className="h-4 w-4" /> Notes</button>
        <button onClick={() => setTab("transcript")} className={`tab-button ${tab === "transcript" ? "tab-active" : ""}`}>Transcript</button>
      </div>
      <div className="mt-7">
        {video.data.status !== "completed" ? (
          <div className="rounded-3xl border border-slate-200 bg-white p-10 text-center">
            <h2 className="text-xl font-bold">This lesson is {video.data.status}</h2>
            <p className="mt-2 text-sm text-slate-500">Notes and quizzes appear here when processing completes.</p>
            {video.data.status === "waiting_for_transcript" ? (
              <p className="mx-auto mt-4 max-w-3xl rounded-2xl bg-sky-50 p-4 text-left text-sm text-sky-800">
                This lesson is queued for the local transcript fetcher. Keep the Windows fetcher running on your home network.
              </p>
            ) : null}
            {video.data.status === "transcribing" ? (
              <p className="mx-auto mt-4 max-w-3xl rounded-2xl bg-blue-50 p-4 text-left text-sm text-blue-800">
                The local fetcher uploaded audio and AWS is transcribing it with Whisper.
              </p>
            ) : null}
            {video.data.status === "failed" && video.data.error_message ? (
              <p className="mx-auto mt-4 max-w-3xl whitespace-pre-wrap rounded-2xl bg-rose-50 p-4 text-left text-sm text-rose-700">
                {video.data.error_message}
              </p>
            ) : null}
            {video.data.status === "failed" ? (
              <button onClick={retryProcessing} disabled={generationAction !== null} className="primary-button mt-5">
                <RefreshCw className="h-4 w-4" /> {generationAction === "retry" ? "Retrying..." : "Retry processing"}
              </button>
            ) : null}
          </div>
        ) : tab === "notes" ? (
          notes.data ? (
            <div>
              <p className="mb-4 text-xs font-semibold text-slate-500">
                Generated with {notes.data.source_model.replace("groq/", "")}
              </p>
              <DiagramPanel videoId={id} />
              <MarkdownViewer markdown={notes.data.full_markdown} />
            </div>
          ) : notes.isLoading ? <div className="h-96 animate-pulse rounded-3xl bg-slate-200" /> : <p className="text-rose-600">Could not load notes.</p>
        ) : transcript.data ? (
          <article className="rounded-3xl border border-slate-200 bg-white p-7 leading-8 text-slate-700 shadow-sm">{transcript.data.full_text}</article>
        ) : transcript.isLoading ? <div className="h-96 animate-pulse rounded-3xl bg-slate-200" /> : <p className="text-rose-600">Could not load transcript.</p>}
      </div>
      {quizOpen ? <QuizFocus videoId={id} onClose={() => setQuizOpen(false)} /> : null}
      {manualOpen ? <ManualAssist videoId={id} onClose={() => setManualOpen(false)} /> : null}
    </div>
  );
}
