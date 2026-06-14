export type DiagramState =
  | "pending"
  | "spec_generating"
  | "rendering"
  | "rate_limited"
  | "completed"
  | "failed"
  | "skipped"
  | "stale";

export type DiagramMode = "structured" | "illustrative";

export interface DiagramAsset {
  id: string;
  video_id: string;
  course_id: string;
  marker_index: number;
  original_caption: string;
  detailed_prompt: string | null;
  alt_text: string | null;
  render_mode: DiagramMode | null;
  mermaid_source: string | null;
  provider: string | null;
  model: string | null;
  state: DiagramState;
  retry_at: string | null;
  image_url: string | null;
  width: number | null;
  height: number | null;
  revision: number;
  error_message: string | null;
}

export interface DiagramStatus {
  course_id: string;
  discovered: number;
  pending: number;
  processing: number;
  waiting: number;
  completed: number;
  failed: number;
  skipped: number;
  stale: number;
}

export interface DiagramGenerateResult {
  course_id: string;
  discovered: number;
  queued: number;
}
