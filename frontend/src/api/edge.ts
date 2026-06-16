import { request } from "./client";

export interface EdgeToken {
  id: string;
  name: string;
  token_prefix: string;
  revoked: boolean;
  last_seen_at: string | null;
  created_at: string | null;
}

export interface EdgeTokenCreated extends EdgeToken {
  token: string;
}

export function listEdgeTokens(): Promise<EdgeToken[]> {
  return request<EdgeToken[]>("/edge/tokens");
}

export function createEdgeToken(name: string): Promise<EdgeTokenCreated> {
  return request<EdgeTokenCreated>("/edge/tokens", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export function revokeEdgeToken(tokenId: string): Promise<void> {
  return request<void>(`/edge/tokens/${tokenId}`, { method: "DELETE" });
}
