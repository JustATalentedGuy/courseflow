import { request } from "./client";
import type { SearchRequest, SearchResult } from "../types";

export function searchNotes(payload: SearchRequest): Promise<SearchResult[]> {
  return request<SearchResult[]>("/search", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function suggestSearch(query: string): Promise<string[]> {
  return request<string[]>(`/search/suggest?q=${encodeURIComponent(query)}`);
}
