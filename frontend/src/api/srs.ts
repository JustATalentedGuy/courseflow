import { request } from "./client";
import type { ConceptCard, SrsStats, StudyPlan } from "../types";

export function getDueCards(): Promise<ConceptCard[]> {
  return request<ConceptCard[]>("/srs/due-today");
}

export function getCards(): Promise<ConceptCard[]> {
  return request<ConceptCard[]>("/srs/cards");
}

export function reviewCard(cardId: string, score: number): Promise<ConceptCard> {
  return request<ConceptCard>("/srs/review", {
    method: "POST",
    body: JSON.stringify({ card_id: cardId, score }),
  });
}

export function getSrsStats(): Promise<SrsStats> {
  return request<SrsStats>("/srs/stats");
}

export function getExamPlan(examDate: string): Promise<StudyPlan> {
  return request<StudyPlan>("/srs/exam-plan", {
    method: "POST",
    body: JSON.stringify({ exam_date: examDate }),
  });
}
