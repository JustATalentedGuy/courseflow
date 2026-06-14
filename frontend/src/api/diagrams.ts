import { request } from "./client";
import type {
  DiagramAsset,
  DiagramGenerateResult,
  DiagramMode,
  DiagramStatus,
} from "../types/diagram";

export function generateCourseDiagrams(courseId: string): Promise<DiagramGenerateResult> {
  return request<DiagramGenerateResult>(`/courses/${courseId}/diagrams/generate`, {
    method: "POST",
  });
}

export function getCourseDiagramStatus(courseId: string): Promise<DiagramStatus> {
  return request<DiagramStatus>(`/courses/${courseId}/diagrams/status`);
}

export function getVideoDiagrams(videoId: string): Promise<DiagramAsset[]> {
  return request<DiagramAsset[]>(`/videos/${videoId}/diagrams`);
}

export function regenerateDiagram(
  diagramId: string,
  payload: { prompt?: string; mode?: DiagramMode },
): Promise<DiagramAsset> {
  return request<DiagramAsset>(`/diagrams/${diagramId}/regenerate`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function removeDiagram(diagramId: string): Promise<void> {
  return request<void>(`/diagrams/${diagramId}`, { method: "DELETE" });
}
