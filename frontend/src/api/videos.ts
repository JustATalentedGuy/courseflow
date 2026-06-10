import { request } from "./client";
import type { NormalisedTranscript } from "../types/transcript";
import type { VideoResponse } from "../types/video";

export function getVideo(videoId: string, accessToken: string): Promise<VideoResponse> {
  return request<VideoResponse>(`/videos/${videoId}`, { accessToken });
}

export function getVideoTranscript(
  videoId: string,
  accessToken: string,
): Promise<NormalisedTranscript> {
  return request<NormalisedTranscript>(`/videos/${videoId}/transcript`, { accessToken });
}
