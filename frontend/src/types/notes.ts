export interface NotesSection {
  heading: string;
  level: 1 | 2 | 3;
  content: string;
  concepts: string[];
}

export interface VideoNotes {
  video_id: string;
  course_id: string;
  title: string;
  source_model: string;
  sections: NotesSection[];
  summary: string;
  full_markdown: string;
  has_images: boolean;
  image_count: number;
  generated_at: string;
  token_count: number;
  prompt_token_count: number;
  completion_token_count: number;
  cached_token_count: number;
  request_count: number;
}
