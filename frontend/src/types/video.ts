export type VideoStatus =
  | "pending"
  | "deferred"
  | "processing"
  | "completed"
  | "failed"
  | "manual";

export interface VideoResponse {
  id: string;
  course_id: string;
  youtube_video_id: string;
  title: string;
  position: number;
  duration_seconds: number | null;
  status: VideoStatus;
  transcript_source: "youtube_captions" | "groq_whisper" | null;
  celery_task_id: string | null;
  scheduled_for: string | null;
  error_message: string | null;
  created_at: string | null;
  updated_at: string | null;
}
