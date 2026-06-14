import { request } from "./client";
import type { ManualNotesResult, ManualPrompt } from "../types";
import type { VideoNotes } from "../types/notes";

export function getVideoNotes(videoId: string, accessToken: string): Promise<VideoNotes> {
  return request<VideoNotes>(`/videos/${videoId}/notes`, { accessToken });
}

export function getVideoNotesRaw(videoId: string, accessToken: string): Promise<string> {
  return request<string>(`/videos/${videoId}/notes/raw`, { accessToken });
}

export function regenerateVideoNotes(
  videoId: string,
  accessToken: string,
  quality?: "standard" | "high",
): Promise<VideoNotes> {
  return request<VideoNotes>(`/videos/${videoId}/notes/regenerate`, {
    method: "POST",
    accessToken,
    body: quality ? JSON.stringify({ quality }) : undefined,
  });
}

export function getCourseNotes(courseId: string, accessToken: string): Promise<VideoNotes[]> {
  return request<VideoNotes[]>(`/courses/${courseId}/notes`, { accessToken });
}

export function getManualPrompt(videoId: string, chunkIndex: number): Promise<ManualPrompt> {
  return request<ManualPrompt>(`/videos/${videoId}/manual-prompt?chunk=${chunkIndex}`);
}

export function submitManualNotes(
  videoId: string,
  chunkIndex: number,
  response: string,
): Promise<ManualNotesResult> {
  return request<ManualNotesResult>(`/videos/${videoId}/manual-notes`, {
    method: "POST",
    body: JSON.stringify({ chunk_index: chunkIndex, response }),
  });
}
