import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, test, vi } from "vitest";
import { Route, Routes } from "react-router-dom";

import { request } from "../api/client";
import { AuthProvider } from "../auth/AuthContext";
import { CourseProgress } from "../components/CourseProgress";
import { ManualAssist } from "../components/ManualAssist";
import { MarkdownViewer } from "../components/MarkdownViewer";
import { QuizFocus } from "../components/QuizFocus";
import { App } from "../pages/App";
import { NewCoursePage } from "../pages/CoursePages";
import { VideoDetailPage } from "../pages/VideoDetailPage";
import { renderWithProviders } from "./render";
import { server } from "./server";

const API = "http://localhost:8000/api/v1";

const course = {
  id: "course-1",
  title: "Algorithms",
  playlist_url: "https://youtube.com/playlist?list=abc",
  playlist_id: "abc",
  video_count: 1,
  status: "processing",
  created_at: "2026-06-01T00:00:00Z",
  updated_at: "2026-06-01T00:00:00Z",
  videos: [{
    id: "video-1",
    course_id: "course-1",
    youtube_video_id: "yt-1",
    title: "Binary Search",
    position: 1,
    duration_seconds: 600,
    status: "processing",
    transcript_source: null,
    celery_task_id: null,
    scheduled_for: null,
    error_message: null,
    created_at: null,
    updated_at: null,
  }],
};

describe("Phase 8 frontend", () => {
  test("root redirects unauthenticated users to login", async () => {
    renderWithProviders(
      <AuthProvider>
        <App />
      </AuthProvider>,
    );

    expect(await screen.findByRole("heading", { name: "Welcome back" })).toBeInTheDocument();
  });

  test("authenticated dashboard route renders the application shell", async () => {
    localStorage.setItem("access_token", "valid");
    server.use(
      http.get(`${API}/srs/stats`, () => HttpResponse.json({
        total_cards: 4,
        due_today: 2,
        retention_rate: 0.75,
        streak: 3,
      })),
      http.get(`${API}/courses`, () => HttpResponse.json([])),
    );

    renderWithProviders(
      <AuthProvider>
        <App />
      </AuthProvider>,
      { initialEntries: ["/dashboard"] },
    );

    expect(
      await screen.findByRole("heading", { name: "Keep the momentum" }, { timeout: 10_000 }),
    ).toBeInTheDocument();
    expect(screen.getByText("CourseFlow")).toBeInTheDocument();
    expect(await screen.findByText("75%")).toBeInTheDocument();
  });

  test("playlist URL input shows error for non-YouTube URL before API call", async () => {
    let calls = 0;
    server.use(http.post(`${API}/courses`, () => {
      calls += 1;
      return HttpResponse.json({});
    }));
    renderWithProviders(<NewCoursePage />);

    await userEvent.type(screen.getByLabelText("YouTube URL"), "https://vimeo.com/123");
    await userEvent.click(screen.getByRole("button", { name: "Import course" }));

    expect(screen.getByText("Enter a valid YouTube video or playlist URL")).toBeInTheDocument();
    expect(calls).toBe(0);
  });

  test("course page polls status until all videos complete", async () => {
    let statusCalls = 0;
    server.use(
      http.get(`${API}/courses/course-1`, () => HttpResponse.json(course)),
      http.get(`${API}/courses/course-1/status`, () => {
        statusCalls += 1;
        return HttpResponse.json({
          course_id: "course-1",
          total: 1,
          pending: 0,
          processing: statusCalls === 1 ? 1 : 0,
          completed: statusCalls === 1 ? 0 : 1,
          failed: 0,
          deferred: 0,
          deferred_until: null,
          quota_remaining: {},
        });
      }),
    );
    renderWithProviders(<CourseProgress courseId="course-1" pollIntervalMs={20} />);

    expect(await screen.findByText("Algorithms")).toBeInTheDocument();
    await waitFor(() => expect(statusCalls).toBe(2));
    await act(() => new Promise((resolve) => setTimeout(resolve, 50)));
    expect(statusCalls).toBe(2);
  });

  test("course notes export remains disabled until processing reaches 100 percent", async () => {
    server.use(
      http.get(`${API}/courses/course-1`, () => HttpResponse.json(course)),
      http.get(`${API}/courses/course-1/status`, () => HttpResponse.json({
        course_id: "course-1",
        total: 1,
        pending: 0,
        processing: 1,
        rate_limited: 0,
        batch_processing: 0,
        completed: 0,
        failed: 0,
        deferred: 0,
        deferred_until: null,
        next_retry_at: null,
        quota_remaining: {},
      })),
    );

    renderWithProviders(<CourseProgress courseId="course-1" />);

    expect(await screen.findByRole("button", { name: "Export Notes" })).toBeDisabled();
  });

  test("completed course opens notes format dialog and downloads markdown", async () => {
    let exportCalls = 0;
    server.use(
      http.get(`${API}/courses/course-1`, () => HttpResponse.json({
        ...course,
        status: "completed",
        videos: [{ ...course.videos[0], status: "completed" }],
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
      http.get(`${API}/courses/course-1/export/notes/markdown`, () => {
        exportCalls += 1;
        return new HttpResponse("# Algorithms", {
          headers: { "Content-Type": "text/markdown" },
        });
      }),
    );
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:test");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);

    renderWithProviders(<CourseProgress courseId="course-1" />);

    await userEvent.click(await screen.findByRole("button", { name: "Export Notes" }));
    expect(screen.getByRole("dialog", { name: "Export course notes" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Markdown" }));
    await waitFor(() => expect(exportCalls).toBe(1));
  });

  test("notes viewer renders markdown headings correctly", () => {
    renderWithProviders(<MarkdownViewer markdown={"## Introduction\nSome text"} />);
    expect(screen.getByRole("heading", { name: "Introduction" })).toBeInTheDocument();
  });

  test("broken image in notes shows placeholder", () => {
    renderWithProviders(<MarkdownViewer markdown={"## Diagram\n![System diagram](https://bad.test/image.png)"} />);
    fireEvent.error(screen.getByAltText("System diagram"));
    expect(screen.getByRole("img", { name: "System diagram" })).toHaveTextContent("System diagram");
  });

  test("quiz submit on empty answer shows validation error", async () => {
    let answerCalls = 0;
    server.use(
      http.post(`${API}/quiz/start`, () => HttpResponse.json({
        session_id: "session-1",
        first_question: "What is binary search?",
        current_concept: "Binary search",
        difficulty: "easy",
      })),
      http.post(`${API}/quiz/answer`, () => {
        answerCalls += 1;
        return HttpResponse.json({});
      }),
    );
    renderWithProviders(<QuizFocus videoId="video-1" onClose={() => undefined} />);
    await userEvent.click(screen.getByRole("button", { name: "Start quiz" }));
    expect(await screen.findByText("What is binary search?")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Submit answer/ }));
    expect(screen.getByText("Please enter an answer")).toBeInTheDocument();
    expect(answerCalls).toBe(0);
  });

  test("401 response triggers token refresh then retries request", async () => {
    localStorage.setItem("access_token", "expired");
    localStorage.setItem("refresh_token", "refresh");
    let protectedCalls = 0;
    server.use(
      http.get(`${API}/protected`, ({ request: incoming }) => {
        protectedCalls += 1;
        return incoming.headers.get("Authorization") === "Bearer fresh"
          ? HttpResponse.json({ ok: true })
          : new HttpResponse(null, { status: 401 });
      }),
      http.post(`${API}/auth/refresh`, () => HttpResponse.json({ access_token: "fresh" })),
    );

    await expect(request<{ ok: boolean }>("/protected")).resolves.toEqual({ ok: true });
    expect(protectedCalls).toBe(2);
    expect(localStorage.getItem("access_token")).toBe("fresh");
  });

  test("deferred video shows correct scheduled date", async () => {
    server.use(
      http.get(`${API}/courses/course-1`, () => HttpResponse.json({
        ...course,
        status: "partial",
        videos: [{ ...course.videos[0], status: "deferred", scheduled_for: "2026-06-10T00:00:30Z" }],
      })),
      http.get(`${API}/courses/course-1/status`, () => HttpResponse.json({
        course_id: "course-1",
        total: 1,
        pending: 0,
        processing: 0,
        completed: 0,
        failed: 0,
        deferred: 1,
        deferred_until: "2026-06-10T00:00:30Z",
        quota_remaining: {},
      })),
    );
    renderWithProviders(<CourseProgress courseId="course-1" />);
    expect(await screen.findByText(/Daily allowance reached\. Resumes Jun 10/)).toBeInTheDocument();
  });

  test("manual assist advances to the next missing chunk", async () => {
    server.use(
      http.get(`${API}/videos/video-1/manual-prompt`, ({ request }) => {
        const chunk = Number(new URL(request.url).searchParams.get("chunk") ?? 0);
        return HttpResponse.json({
          prompt_text: `Prompt for chunk ${chunk + 1}`,
          chunk_index: chunk,
          total_chunks: 2,
          estimated_tokens: 500,
          video_title: "Binary Search",
        });
      }),
      http.post(`${API}/videos/video-1/manual-notes`, () => HttpResponse.json({
        status: "partial",
        notes_id: null,
        received_chunks: [0],
        total_chunks: 2,
      })),
    );
    renderWithProviders(<ManualAssist videoId="video-1" onClose={() => undefined} />);

    expect(await screen.findByText("Chunk 1 of 2")).toBeInTheDocument();
    await userEvent.type(
      screen.getByLabelText("LLM Markdown response"),
      "## Binary Search\n\nA complete explanation.\n\nKey Concepts:\n- binary search",
    );
    await userEvent.click(screen.getByRole("button", { name: "Save chunk" }));

    expect(await screen.findByText("Chunk 2 of 2")).toBeInTheDocument();
  });

  test("high-quality regeneration explicitly requests the 70B profile", async () => {
    let requestedQuality = "";
    server.use(
      http.get(`${API}/videos/video-1`, () => HttpResponse.json({
        ...course.videos[0],
        status: "completed",
      })),
      http.get(`${API}/videos/video-1/notes`, () => HttpResponse.json({
        video_id: "video-1",
        course_id: "course-1",
        title: "Binary Search",
        source_model: "groq/meta-llama/llama-4-scout-17b-16e-instruct",
        sections: [{ heading: "Binary Search", level: 2, content: "Notes.", concepts: ["search"] }],
        summary: "Binary search finds values efficiently.",
        full_markdown: "## Binary Search\n\nNotes.",
        has_images: false,
        image_count: 0,
        generated_at: "2026-06-12T00:00:00Z",
        token_count: 20,
        prompt_token_count: 15,
        completion_token_count: 5,
        cached_token_count: 0,
        request_count: 1,
      })),
      http.post(`${API}/videos/video-1/notes/regenerate`, async ({ request: incoming }) => {
        requestedQuality = ((await incoming.json()) as { quality: string }).quality;
        return HttpResponse.json({
          video_id: "video-1",
          course_id: "course-1",
          title: "Binary Search",
          source_model: "groq/llama-3.3-70b-versatile",
          sections: [{ heading: "Binary Search", level: 2, content: "Better notes.", concepts: ["search"] }],
          summary: "Binary search finds values efficiently.",
          full_markdown: "## Binary Search\n\nBetter notes.",
          has_images: false,
          image_count: 0,
          generated_at: "2026-06-12T00:00:00Z",
          token_count: 24,
          prompt_token_count: 16,
          completion_token_count: 8,
          cached_token_count: 0,
          request_count: 1,
        });
      }),
    );

    renderWithProviders(
      <Routes>
        <Route path="/videos/:id" element={<VideoDetailPage />} />
      </Routes>,
      { initialEntries: ["/videos/video-1"] },
    );

    await userEvent.click(await screen.findByRole("button", { name: "High quality (70B)" }));
    await waitFor(() => expect(requestedQuality).toBe("high"));
    expect(await screen.findByText("Generated with llama-3.3-70b-versatile")).toBeInTheDocument();
  });

  test("failed video displays its reason and can be retried", async () => {
    let retryCalls = 0;
    server.use(
      http.get(`${API}/videos/video-1`, () => HttpResponse.json({
        ...course.videos[0],
        status: "failed",
        error_message: "Generated notes did not pass validation.",
      })),
      http.post(`${API}/videos/video-1/retry`, () => {
        retryCalls += 1;
        return HttpResponse.json({
          ...course.videos[0],
          status: "pending",
          error_message: null,
        });
      }),
    );

    renderWithProviders(
      <Routes>
        <Route path="/videos/:id" element={<VideoDetailPage />} />
      </Routes>,
      { initialEntries: ["/videos/video-1"] },
    );

    expect(await screen.findByText("Generated notes did not pass validation.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry processing" }));
    await waitFor(() => expect(retryCalls).toBe(1));
  });
});
