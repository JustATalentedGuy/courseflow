import type { VideoResponse } from "./video";

export interface CourseCreate {
  playlist_url: string;
}

export interface CourseResponse {
  id: string;
  title: string;
  playlist_url: string;
  playlist_id: string;
  video_count: number;
  status: "pending" | "processing" | "completed" | "partial";
  created_at: string | null;
  updated_at: string | null;
}

export interface CourseDetail extends CourseResponse {
  videos: VideoResponse[];
}

export interface CourseStatusResponse {
  course_id: string;
  total: number;
  pending: number;
  processing: number;
  rate_limited: number;
  batch_processing: number;
  completed: number;
  failed: number;
  deferred: number;
  deferred_until: string | null;
  next_retry_at: string | null;
  quota_remaining: {
    llm_requests?: number;
    llm_tokens?: number;
    whisper_requests?: number;
    whisper_audio_seconds_hour?: number;
    whisper_audio_seconds_day?: number;
  };
}
