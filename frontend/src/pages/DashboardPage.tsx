import { useQuery } from "@tanstack/react-query";
import { ArrowRight, BookOpen, Brain, Flame, Gauge } from "lucide-react";
import { Link } from "react-router-dom";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { getCourse, listCourses } from "../api/courses";
import { getQuizHistory } from "../api/quiz";
import { getSrsStats } from "../api/srs";
import { StatusChip } from "../components/StatusChip";

async function loadDashboardActivity() {
  const courses = await listCourses("");
  const details = await Promise.all(courses.slice(0, 5).map((course) => getCourse(course.id, "")));
  const videos = details.flatMap((course) => course.videos).sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? "")).slice(0, 5);
  const histories = await Promise.all(videos.filter((video) => video.status === "completed").map((video) => getQuizHistory(video.id).catch(() => [])));
  const scores = histories.flat().sort((a, b) => a.completed_at.localeCompare(b.completed_at)).slice(-30).map((result) => ({
    date: new Date(result.completed_at).toLocaleDateString("en-US", { month: "short", day: "numeric" }),
    score: Math.round(result.average_score * 100),
  }));
  return { videos, scores };
}

export function DashboardPage() {
  const stats = useQuery({ queryKey: ["srs-stats"], queryFn: getSrsStats });
  const activity = useQuery({ queryKey: ["dashboard-activity"], queryFn: loadDashboardActivity });
  const quotaRemaining = 1000;

  return (
    <div>
      <p className="eyebrow">Overview</p>
      <h1 className="page-title">Keep the momentum</h1>
      <div className="mt-8 grid gap-5 md:grid-cols-2 xl:grid-cols-4">
        <Metric icon={Brain} label="Due today" value={stats.data?.due_today ?? 0} detail="concept cards" />
        <Metric icon={Gauge} label="Retention" value={`${Math.round((stats.data?.retention_rate ?? 0) * 100)}%`} detail="review accuracy" />
        <Metric icon={Flame} label="Study streak" value={stats.data?.streak ?? 0} detail="consecutive days" />
        <Metric icon={BookOpen} label="Total cards" value={stats.data?.total_cards ?? 0} detail="in your library" />
      </div>
      <div className="mt-6 grid gap-6 xl:grid-cols-[1.4fr_1fr]">
        <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex items-center justify-between"><div><h2 className="font-bold">Retention trend</h2><p className="mt-1 text-sm text-slate-500">Recent completed quiz sessions</p></div></div>
          <div className="mt-6 h-64">
            {activity.data?.scores.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={activity.data.scores}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                  <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                  <YAxis domain={[0, 100]} tick={{ fontSize: 11 }} />
                  <Tooltip />
                  <Line type="monotone" dataKey="score" stroke="#2563eb" strokeWidth={3} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            ) : <div className="grid h-full place-items-center text-sm text-slate-400">Complete a quiz to begin your trend.</div>}
          </div>
        </section>
        <section className="rounded-3xl bg-slate-950 p-6 text-white shadow-sm">
          <p className="text-sm font-semibold text-blue-300">Daily Groq quota</p>
          <p className="mt-3 text-3xl font-bold">{quotaRemaining} / 1000</p>
          <p className="mt-1 text-sm text-slate-400">LLM requests remaining today</p>
          <div className="mt-6 h-2 overflow-hidden rounded-full bg-white/10"><div className="h-full w-full rounded-full bg-blue-400" /></div>
          <p className="mt-4 text-xs text-slate-400">Exact live quota appears after opening a course status page.</p>
          <Link to="/review" className="mt-8 flex items-center justify-between rounded-2xl bg-white px-4 py-4 font-semibold text-slate-950">
            Start today's review <ArrowRight className="h-4 w-4" />
          </Link>
        </section>
      </div>
      <section className="mt-6 rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex items-center justify-between"><h2 className="font-bold">Recent activity</h2><Link to="/courses" className="text-sm font-semibold text-blue-600">View courses</Link></div>
        <div className="mt-4 divide-y divide-slate-100">
          {activity.data?.videos.length ? activity.data.videos.map((video) => (
            <Link to={`/videos/${video.id}`} key={video.id} className="flex items-center justify-between gap-4 py-4">
              <div className="min-w-0"><p className="truncate font-semibold">{video.title}</p><p className="mt-1 text-xs text-slate-400">Lesson {video.position}</p></div>
              <StatusChip status={video.status} />
            </Link>
          )) : <p className="py-8 text-center text-sm text-slate-400">Your processed lessons will appear here.</p>}
        </div>
      </section>
    </div>
  );
}

function Metric({ icon: Icon, label, value, detail }: { icon: typeof Brain; label: string; value: string | number; detail: string }) {
  return (
    <div className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
      <span className="grid h-10 w-10 place-items-center rounded-xl bg-blue-50 text-blue-600"><Icon className="h-5 w-5" /></span>
      <p className="mt-5 text-sm font-semibold text-slate-500">{label}</p>
      <p className="mt-1 text-3xl font-bold">{value}</p>
      <p className="mt-1 text-xs text-slate-400">{detail}</p>
    </div>
  );
}
