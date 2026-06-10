export interface TextChunk {
  chunk_id: string;
  video_id: string;
  course_id: string;
  user_id: string;
  text: string;
  start_seconds: number;
  end_seconds: number;
  section_heading: string;
  embedding: number[];
  chunk_index: number;
}
