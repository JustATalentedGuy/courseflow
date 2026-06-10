import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Clock3, Download } from "lucide-react";
import { Link } from "react-router-dom";

import { downloadFile } from "../api/client";
import { getCourse, getCourseStatus } from "../api/courses";
import { StatusChip } from "./StatusChip";

export function formatScheduledDate(value: string): string {
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" }).format(new Date(value));
}

export function CourseProgress({ courseId, pollIntervalMs = 5000 }: { courseId: string; pollIntervalMs?: number }) {
  const [exporting, setExporting] = useState(false);
  const courseQuery = useQuery({
    queryKey: ["course", courseId],
    queryFn: () => getCourse(courseId, ""),
  });
  const statusQuery = useQuery({
    queryKey: ["course-status", courseId],
    queryFn: () => getCourseStatus(courseId, ""),
    refetchInterval: (query) => {
      const status = query.state.data;
      return status && status.pending + status.processing === 0 ? false : pollIntervalMs;
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

  async function exportAnki() {
    setExporting(true);
    try {
      await downloadFile(`/courses/${courseId}/export/anki`, "courseflow.apkg");
    } finally {
      setExporting(false);
    }
  }

  return (
    <div>
      <div className="mb-7 rounded-3xl bg-gradient-to-br from-slate-950 to-slate-800 p-7 text-white">
        <div className="flex items-start justify-between gap-4">
          <p className="text-sm font-semibold text-blue-300">Course progress</p>
          <button onClick={exportAnki} disabled={exporting} className="inline-flex items-center gap-2 rounded-xl bg-white/10 px-4 py-2 text-sm font-semibold hover:bg-white/15 disabled:opacity-50">
            <Download className="h-4 w-4" /> {exporting ? "Preparing..." : "Export Anki"}
          </button>
        </div>
        <h1 className="mt-2 text-3xl font-bold">{course.title}</h1>
        <div className="mt-6 flex items-center gap-4">
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-white/15">
            <div className="h-full rounded-full bg-blue-400 transition-all" style={{ width: `${progress}%` }} />
          </div>
          <span className="text-sm font-semibold">{progress}%</span>
        </div>
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
                  <Clock3 className="h-3 w-3" /> Scheduled for {formatScheduledDate(video.scheduled_for)}
                </p>
              ) : null}
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
