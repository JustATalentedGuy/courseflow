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
  source_model:
    | "groq/llama-3.3-70b"
    | "manual/claude"
    | "manual/other"
    | "manual/user";
  sections: NotesSection[];
  summary: string;
  full_markdown: string;
  has_images: boolean;
  image_count: number;
  generated_at: string;
  token_count: number;
}
