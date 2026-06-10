import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Clipboard, Download, FileDown, FileText, HelpCircle, WandSparkles } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import { getVideoNotes } from "../api/notes";
import { downloadFile } from "../api/client";
import { getVideo, getVideoTranscript } from "../api/videos";
import { ManualAssist } from "../components/ManualAssist";
import { MarkdownViewer } from "../components/MarkdownViewer";
import { QuizFocus } from "../components/QuizFocus";

export function VideoDetailPage() {
  const { id = "" } = useParams();
  const [tab, setTab] = useState<"notes" | "transcript">("notes");
  const [quizOpen, setQuizOpen] = useState(false);
  const [manualOpen, setManualOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [downloading, setDownloading] = useState<"markdown" | "pdf" | null>(null);
  const video = useQuery({ queryKey: ["video", id], queryFn: () => getVideo(id, ""), enabled: Boolean(id) });
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
          <button onClick={() => setManualOpen(true)} className="secondary-button"><WandSparkles className="h-4 w-4" /> Generate manually</button>
        </div>
      </div>
      <div className="mt-8 flex gap-2 border-b border-slate-200">
        <button onClick={() => setTab("notes")} className={`tab-button ${tab === "notes" ? "tab-active" : ""}`}><FileText className="h-4 w-4" /> Notes</button>
        <button onClick={() => setTab("transcript")} className={`tab-button ${tab === "transcript" ? "tab-active" : ""}`}>Transcript</button>
      </div>
      <div className="mt-7">
        {video.data.status !== "completed" ? (
          <div className="rounded-3xl border border-slate-200 bg-white p-10 text-center">
            <h2 className="text-xl font-bold">This lesson is {video.data.status}</h2>
            <p className="mt-2 text-sm text-slate-500">Notes and quizzes appear here when processing completes.</p>
          </div>
        ) : tab === "notes" ? (
          notes.data ? <MarkdownViewer markdown={notes.data.full_markdown} /> : notes.isLoading ? <div className="h-96 animate-pulse rounded-3xl bg-slate-200" /> : <p className="text-rose-600">Could not load notes.</p>
        ) : transcript.data ? (
          <article className="rounded-3xl border border-slate-200 bg-white p-7 leading-8 text-slate-700 shadow-sm">{transcript.data.full_text}</article>
        ) : transcript.isLoading ? <div className="h-96 animate-pulse rounded-3xl bg-slate-200" /> : <p className="text-rose-600">Could not load transcript.</p>}
      </div>
      {quizOpen ? <QuizFocus videoId={id} onClose={() => setQuizOpen(false)} /> : null}
      {manualOpen ? <ManualAssist videoId={id} onClose={() => setManualOpen(false)} /> : null}
    </div>
  );
}
