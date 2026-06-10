import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, BookOpen, Plus } from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { createCourse, listCourses } from "../api/courses";
import { CourseProgress } from "../components/CourseProgress";
import { EmptyState } from "../components/EmptyState";
import { StatusChip } from "../components/StatusChip";

export const YOUTUBE_URL_PATTERN = /^https?:\/\/(www\.)?(youtube\.com\/(watch\?.*v=|playlist\?.*list=)|youtu\.be\/)[^\s]+$/i;

export function CoursesPage() {
  const query = useQuery({ queryKey: ["courses"], queryFn: () => listCourses("") });
  return (
    <div>
      <div className="flex items-end justify-between gap-4">
        <div><p className="eyebrow">Library</p><h1 className="page-title">Your courses</h1></div>
        <Link to="/courses/new" className="primary-button"><Plus className="h-4 w-4" /> Add course</Link>
      </div>
      {query.data?.length ? (
        <div className="mt-8 grid gap-5 md:grid-cols-2 xl:grid-cols-3">
          {query.data.map((course) => (
            <Link to={`/courses/${course.id}`} key={course.id} className="group rounded-3xl border border-slate-200 bg-white p-6 shadow-sm transition hover:-translate-y-1 hover:shadow-lg">
              <span className="grid h-12 w-12 place-items-center rounded-2xl bg-blue-50 text-blue-600"><BookOpen /></span>
              <h2 className="mt-5 line-clamp-2 text-lg font-bold">{course.title}</h2>
              <div className="mt-5 flex items-center justify-between text-sm text-slate-500">
                <span>{course.video_count} videos</span>
                <StatusChip status={course.status === "partial" ? "partial" : course.status === "processing" ? "processing" : course.status === "completed" ? "completed" : "pending"} />
              </div>
            </Link>
          ))}
        </div>
      ) : query.isLoading ? <div className="mt-8 h-48 animate-pulse rounded-3xl bg-slate-200" /> : (
        <div className="mt-8"><EmptyState title="No courses yet" description="Paste a YouTube playlist and CourseFlow will build your learning workspace." action={<Link className="primary-button" to="/courses/new">Add your first course</Link>} /></div>
      )}
    </div>
  );
}

export function NewCoursePage() {
  const [url, setUrl] = useState("");
  const [validation, setValidation] = useState("");
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: () => createCourse({ playlist_url: url }, ""),
    onSuccess(course) {
      void queryClient.invalidateQueries({ queryKey: ["courses"] });
      navigate(`/courses/${course.id}`);
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!YOUTUBE_URL_PATTERN.test(url.trim())) {
      setValidation("Enter a valid YouTube video or playlist URL");
      return;
    }
    setValidation("");
    mutation.mutate();
  }

  return (
    <div className="mx-auto max-w-2xl">
      <Link to="/courses" className="inline-flex items-center gap-2 text-sm font-semibold text-slate-500 hover:text-slate-900"><ArrowLeft className="h-4 w-4" /> Courses</Link>
      <div className="mt-7 rounded-3xl border border-slate-200 bg-white p-7 shadow-sm sm:p-10">
        <p className="eyebrow">New course</p>
        <h1 className="mt-2 text-3xl font-bold">Import a YouTube course</h1>
        <p className="mt-3 leading-7 text-slate-500">Paste a playlist or video URL. We will create the course immediately and process each lesson in the background.</p>
        <form onSubmit={submit} className="mt-8">
          <label htmlFor="playlist-url" className="text-sm font-semibold">YouTube URL</label>
          <input id="playlist-url" value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://www.youtube.com/playlist?list=..." className="mt-2 w-full rounded-xl border border-slate-200 px-4 py-3 outline-none focus:border-blue-500" />
          {validation ? <p className="mt-2 text-sm text-rose-600">{validation}</p> : null}
          {mutation.error ? <p className="mt-2 text-sm text-rose-600">{mutation.error.message}</p> : null}
          <button disabled={mutation.isPending} className="primary-button mt-6">{mutation.isPending ? "Importing..." : "Import course"}</button>
        </form>
      </div>
    </div>
  );
}

export function CourseDetailPage() {
  const { id } = useParams();
  return id ? <CourseProgress courseId={id} /> : null;
}
