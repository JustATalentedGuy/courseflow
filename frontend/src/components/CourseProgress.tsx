import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Clock3, Download, Images } from "lucide-react";
import { Link } from "react-router-dom";

import { downloadFile } from "../api/client";
import { getCourse, getCourseStatus } from "../api/courses";
import { generateCourseDiagrams, getCourseDiagramStatus } from "../api/diagrams";
import { StatusChip } from "./StatusChip";

export function formatScheduledDate(value: string): string {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

export function formatResumeTime(value: string): string {
  const seconds = Math.max(0, Math.ceil((new Date(value).getTime() - Date.now()) / 1000));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.ceil(seconds / 60)}m`;
  return `${Math.ceil(seconds / 3600)}h`;
}

export function CourseProgress({ courseId, pollIntervalMs = 5000 }: { courseId: string; pollIntervalMs?: number }) {
  const [exporting, setExporting] = useState(false);
  const [notesExportOpen, setNotesExportOpen] = useState(false);
  const [notesFormat, setNotesFormat] = useState<"markdown" | "pdf" | null>(null);
  const [diagramTracking, setDiagramTracking] = useState(false);
  const [diagramStarting, setDiagramStarting] = useState(false);
  const courseQuery = useQuery({
    queryKey: ["course", courseId],
    queryFn: () => getCourse(courseId, ""),
  });
  const statusQuery = useQuery({
    queryKey: ["course-status", courseId],
    queryFn: () => getCourseStatus(courseId, ""),
    refetchInterval: (query) => {
      const status = query.state.data;
      return status &&
        status.pending +
          status.processing +
          (status.rate_limited ?? 0) +
          (status.batch_processing ?? 0) === 0
        ? false
        : pollIntervalMs;
    },
  });
  const diagramStatus = useQuery({
    queryKey: ["course-diagrams", courseId],
    queryFn: () => getCourseDiagramStatus(courseId),
    enabled: diagramTracking,
    refetchInterval: (query) => {
      const data = query.state.data;
      return data && data.pending + data.processing + data.waiting > 0 ? 4000 : false;
    },
  });

  useEffect(() => {
    if (statusQuery.dataUpdatedAt) void courseQuery.refetch();
  }, [statusQuery.dataUpdatedAt]);

  if (courseQuery.isLoading) return <div className="h-48 animate-pulse rounded-3xl bg-slate-200" />;
  if (courseQuery.error || !courseQuery.data) return <p className="text-rose-600">Could not load this course.</p>;

  const course = courseQuery.data;
  const completed = statusQuery.data?.completed ?? course.videos.filter((video) => video.status === "completed").length;
  const progress = course.video_count ? Math.round((completed / course.video_count) * 100) : 0;
  const courseComplete = course.video_count > 0 && completed === course.video_count;

  async function exportAnki() {
    setExporting(true);
    try {
      await downloadFile(`/courses/${courseId}/export/anki`, "courseflow.apkg");
    } finally {
      setExporting(false);
    }
  }

  async function exportCourseNotes(format: "markdown" | "pdf") {
    setNotesFormat(format);
    try {
      const extension = format === "markdown" ? "md" : "pdf";
      const slug = course.title
        .replace(/[^A-Za-z0-9._-]+/g, "-")
        .replace(/^-|-$/g, "") || "courseflow";
      await downloadFile(
        `/courses/${courseId}/export/notes/${format}`,
        `${slug}-notes.${extension}`,
      );
      setNotesExportOpen(false);
    } finally {
      setNotesFormat(null);
    }
  }

  async function startDiagrams() {
    setDiagramStarting(true);
    try {
      await generateCourseDiagrams(courseId);
      setDiagramTracking(true);
      await diagramStatus.refetch();
    } finally {
      setDiagramStarting(false);
    }
  }

  return (
    <div>
      <div className="mb-7 rounded-3xl bg-gradient-to-br from-slate-950 to-slate-800 p-7 text-white">
        <div className="flex items-start justify-between gap-4">
          <p className="text-sm font-semibold text-blue-300">Course progress</p>
          <div className="flex flex-col items-stretch gap-2">
            <button onClick={exportAnki} disabled={exporting} className="inline-flex items-center justify-center gap-2 rounded-xl bg-white/10 px-4 py-2 text-sm font-semibold hover:bg-white/15 disabled:opacity-50">
              <Download className="h-4 w-4" /> {exporting ? "Preparing..." : "Export Anki"}
            </button>
            <button
              onClick={() => setNotesExportOpen(true)}
              disabled={!courseComplete}
              title={courseComplete ? "Export all course notes" : "Available when the course is 100% processed"}
              className="inline-flex items-center justify-center gap-2 rounded-xl bg-white/10 px-4 py-2 text-sm font-semibold hover:bg-white/15 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Download className="h-4 w-4" /> Export Notes
            </button>
            <button
              onClick={startDiagrams}
              disabled={!courseComplete || diagramStarting}
              title={courseComplete ? "Generate missing course diagrams" : "Available when the course is 100% processed"}
              className="inline-flex items-center justify-center gap-2 rounded-xl bg-blue-400/20 px-4 py-2 text-sm font-semibold text-blue-100 hover:bg-blue-400/30 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Images className="h-4 w-4" /> {diagramStarting ? "Discovering..." : "Generate Diagrams"}
            </button>
          </div>
        </div>
        <h1 className="mt-2 text-3xl font-bold">{course.title}</h1>
        <div className="mt-6 flex items-center gap-4">
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-white/15">
            <div className="h-full rounded-full bg-blue-400 transition-all" style={{ width: `${progress}%` }} />
          </div>
          <span className="text-sm font-semibold">{progress}%</span>
        </div>
        {diagramStatus.data ? (
          <p className="mt-4 text-sm text-slate-300">
            Diagrams: {diagramStatus.data.completed}/{diagramStatus.data.discovered} complete
            {diagramStatus.data.processing + diagramStatus.data.pending ? `, ${diagramStatus.data.processing + diagramStatus.data.pending} processing` : ""}
            {diagramStatus.data.waiting ? `, ${diagramStatus.data.waiting} waiting for quota` : ""}
            {diagramStatus.data.failed ? `, ${diagramStatus.data.failed} failed` : ""}
          </p>
        ) : null}
      </div>
      <div className="space-y-3">
        {course.videos.map((video) => (
          <Link key={video.id} to={`/videos/${video.id}`} className="flex items-center gap-4 rounded-2xl border border-slate-200 bg-white p-4 transition hover:-translate-y-0.5 hover:shadow-md">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-slate-100 text-sm font-bold text-slate-500">{video.position}</span>
            <div className="min-w-0 flex-1">
              <h2 className="truncate font-semibold text-slate-900">{video.title}</h2>
              <p className="mt-1 text-xs text-slate-500">{video.duration_seconds ? `${Math.round(video.duration_seconds / 60)} min` : "Duration unavailable"}</p>
            </div>
            <div className="text-right">
              <StatusChip status={video.status} />
              {video.status === "deferred" && video.scheduled_for ? (
                <p className="mt-2 flex items-center gap-1 text-xs text-violet-600">
                  <Clock3 className="h-3 w-3" /> Daily allowance reached. Resumes {formatScheduledDate(video.scheduled_for)}
                </p>
              ) : null}
              {video.status === "rate_limited" && video.scheduled_for ? (
                <p className="mt-2 flex items-center gap-1 text-xs text-cyan-700">
                  <Clock3 className="h-3 w-3" /> Resumes in {formatResumeTime(video.scheduled_for)}
                </p>
              ) : null}
              {video.status === "batch_processing" ? (
                <p className="mt-2 flex items-center gap-1 text-xs text-indigo-700">
                  <Clock3 className="h-3 w-3" /> Billed Batch processing
                </p>
              ) : null}
              {video.status === "failed" && video.error_message ? (
                <p className="mt-2 max-w-sm text-xs text-rose-700" title={video.error_message}>
                  {video.error_message.split("\n")[0]}
                </p>
              ) : null}
            </div>
          </Link>
        ))}
      </div>
      {notesExportOpen ? (
        <div className="fixed inset-0 z-50 grid place-items-center bg-slate-950/60 p-4">
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="export-notes-title"
            className="w-full max-w-md rounded-3xl bg-white p-6 text-slate-900 shadow-2xl"
          >
            <h2 id="export-notes-title" className="text-xl font-bold">Export course notes</h2>
            <p className="mt-2 text-sm text-slate-500">Choose a format for the combined notes from all {course.video_count} lessons.</p>
            <div className="mt-6 grid gap-3">
              <button
                onClick={() => exportCourseNotes("markdown")}
                disabled={notesFormat !== null}
                className="primary-button justify-center"
              >
                {notesFormat === "markdown" ? "Preparing Markdown..." : "Markdown"}
              </button>
              <button
                onClick={() => exportCourseNotes("pdf")}
                disabled={notesFormat !== null}
                className="secondary-button justify-center"
              >
                {notesFormat === "pdf" ? "Preparing PDF..." : "PDF"}
              </button>
              <button
                onClick={() => setNotesExportOpen(false)}
                disabled={notesFormat !== null}
                className="rounded-xl px-4 py-2 text-sm font-semibold text-slate-500 hover:bg-slate-100"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
