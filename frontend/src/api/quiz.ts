import { request } from "./client";
import type {
  QuizAnswerRequest,
  QuizAnswerResponse,
  QuizResult,
  QuizStartRequest,
  QuizStartResponse,
  WeakConceptSummary,
} from "../types";

export function startQuiz(payload: QuizStartRequest): Promise<QuizStartResponse> {
  return request<QuizStartResponse>("/quiz/start", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function answerQuiz(payload: QuizAnswerRequest): Promise<QuizAnswerResponse> {
  return request<QuizAnswerResponse>("/quiz/answer", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getQuizHistory(videoId: string): Promise<QuizResult[]> {
  return request<QuizResult[]>(`/quiz/sessions/${videoId}`);
}

export function getWeakConcepts(): Promise<WeakConceptSummary[]> {
  return request<WeakConceptSummary[]>("/quiz/weak-concepts");
}
