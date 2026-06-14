export * from "./auth";
export * from "./chunk";
export * from "./course";
export * from "./diagram";
export * from "./notes";
export * from "./quota";
export * from "./transcript";
export * from "./video";

export type QuizMode = "quick_drill" | "full_review" | "weak_spot";
export type QuizDifficulty = "easy" | "medium" | "hard";

export interface SearchRequest {
  query: string;
  course_id?: string | null;
  top_k?: number;
}

export interface SearchResult {
  chunk_id: string;
  video_id: string;
  video_title: string;
  course_id: string;
  section_heading: string;
  text: string;
  similarity_score: number;
  start_seconds: number;
  timestamp_url: string;
}

export interface QuizStartRequest {
  video_id: string;
  mode: QuizMode;
}

export interface QuizStartResponse {
  session_id: string;
  first_question: string;
  current_concept: string;
  difficulty: QuizDifficulty;
}

export interface QuizAnswerRequest {
  session_id: string;
  answer: string;
}

export interface QuizAnswerResponse {
  score: number;
  feedback: string;
  next_question: string | null;
  current_concept: string | null;
  difficulty: QuizDifficulty;
  session_complete: boolean;
}

export interface QuizResult {
  id: string;
  video_id: string;
  session_id: string;
  mode: QuizMode;
  total_questions: number;
  average_score: number;
  weak_concepts: string[];
  results_json: Array<Record<string, unknown>>;
  completed_at: string;
}

export interface WeakConceptSummary {
  concept: string;
  attempts: number;
  average_score: number;
  video_ids: string[];
}

export interface ConceptCard {
  id: string;
  video_id: string;
  concept: string;
  ease_factor: number;
  interval_days: number;
  repetitions: number;
  next_review_date: string;
  last_score: number | null;
  last_reviewed_at: string | null;
  created_at: string | null;
}

export interface SrsStats {
  total_cards: number;
  due_today: number;
  retention_rate: number;
  streak: number;
}

export interface StudyPlanDay {
  date: string;
  scheduled_count: number;
  recommended_count: number;
  capacity: number;
}

export interface StudyPlan {
  exam_date: string;
  days_remaining: number;
  total_cards: number;
  daily_capacity: number;
  days: StudyPlanDay[];
  overloaded_dates: string[];
  unscheduled_card_count: number;
  can_complete: boolean;
  recommended_start_date: string | null;
  message: string;
}

export interface ManualPrompt {
  prompt_text: string;
  chunk_index: number;
  total_chunks: number;
  estimated_tokens: number;
  video_title: string;
}

export interface ManualNotesResult {
  status: "partial" | "complete";
  notes_id: string | null;
  received_chunks: number[];
  total_chunks: number;
}
