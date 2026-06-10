import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, test, vi } from "vitest";

import { request } from "../api/client";
import { AuthProvider } from "../auth/AuthContext";
import { CourseProgress } from "../components/CourseProgress";
import { ManualAssist } from "../components/ManualAssist";
import { MarkdownViewer } from "../components/MarkdownViewer";
import { QuizFocus } from "../components/QuizFocus";
import { App } from "../pages/App";
import { NewCoursePage } from "../pages/CoursePages";
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
    expect(await screen.findByText("Scheduled for Jun 10")).toBeInTheDocument();
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
});
