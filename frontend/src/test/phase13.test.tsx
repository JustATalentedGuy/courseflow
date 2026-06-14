import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, test } from "vitest";

import { CourseProgress } from "../components/CourseProgress";
import { DiagramPanel } from "../components/DiagramPanel";
import { renderWithProviders } from "./render";
import { server } from "./server";

const API = "http://localhost:8000/api/v1";

describe("diagram enrichment", () => {
  test("completed course starts diagram enrichment and displays progress", async () => {
    let generateCalls = 0;
    server.use(
      http.get(`${API}/courses/course-1`, () => HttpResponse.json({
        id: "course-1",
        title: "Systems Design",
        playlist_url: "https://youtube.com/playlist?list=systems",
        playlist_id: "systems",
        video_count: 1,
        status: "completed",
        created_at: null,
        updated_at: null,
        videos: [{
          id: "video-1",
          course_id: "course-1",
          youtube_video_id: "yt-1",
          title: "Indexes",
          position: 1,
          duration_seconds: 600,
          status: "completed",
          transcript_source: "youtube_captions",
          celery_task_id: null,
          scheduled_for: null,
          error_message: null,
          created_at: null,
          updated_at: null,
        }],
      })),
      http.get(`${API}/courses/course-1/status`, () => HttpResponse.json({
        course_id: "course-1",
        total: 1,
        pending: 0,
        processing: 0,
        rate_limited: 0,
        batch_processing: 0,
        completed: 1,
        failed: 0,
        deferred: 0,
        deferred_until: null,
        next_retry_at: null,
        quota_remaining: {},
      })),
      http.post(`${API}/courses/course-1/diagrams/generate`, () => {
        generateCalls += 1;
        return HttpResponse.json({ course_id: "course-1", discovered: 2, queued: 2 });
      }),
      http.get(`${API}/courses/course-1/diagrams/status`, () => HttpResponse.json({
        course_id: "course-1",
        discovered: 2,
        pending: 1,
        processing: 0,
        waiting: 0,
        completed: 1,
        failed: 0,
        skipped: 0,
        stale: 0,
      })),
    );

    renderWithProviders(<CourseProgress courseId="course-1" />);
    await userEvent.click(await screen.findByRole("button", { name: "Generate Diagrams" }));
    await waitFor(() => expect(generateCalls).toBe(1));
    expect(await screen.findByText(/Diagrams: 1\/2 complete/)).toBeInTheDocument();
  });

  test("diagram panel edits mode and retries a failed diagram", async () => {
    let payload: Record<string, unknown> = {};
    server.use(
      http.get(`${API}/videos/video-1/diagrams`, () => HttpResponse.json([{
        id: "diagram-1",
        video_id: "video-1",
        course_id: "course-1",
        marker_index: 0,
        original_caption: "B-tree node split",
        detailed_prompt: "Show a full B-tree node splitting into two child nodes.",
        alt_text: "A B-tree node split.",
        render_mode: "structured",
        mermaid_source: null,
        provider: null,
        model: null,
        state: "failed",
        retry_at: null,
        image_url: null,
        width: null,
        height: null,
        revision: 0,
        error_message: "Renderer unavailable",
      }])),
      http.post(`${API}/diagrams/diagram-1/regenerate`, async ({ request }) => {
        payload = await request.json() as Record<string, unknown>;
        return HttpResponse.json({});
      }),
    );

    renderWithProviders(<DiagramPanel videoId="video-1" />);
    await userEvent.selectOptions(await screen.findByRole("combobox"), "illustrative");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(payload.mode).toBe("illustrative"));
    expect(payload.prompt).toContain("B-tree node");
  });
});
