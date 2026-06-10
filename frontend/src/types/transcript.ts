export interface TranscriptSegment {
  start: number;
  end: number;
  text: string;
  speaker: string | null;
}

export interface NormalisedTranscript {
  video_id: string;
  source: "youtube_captions" | "groq_whisper";
  language: string;
  duration_seconds: number;
  segments: TranscriptSegment[];
  full_text: string;
  word_count: number;
  fetched_at: string;
}
