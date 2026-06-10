import { request } from "./client";
import type {
  AccessToken,
  LoginRequest,
  LogoutRequest,
  RefreshRequest,
  RegisterRequest,
  TokenPair,
  UserResponse,
} from "../types/auth";

export function register(payload: RegisterRequest): Promise<UserResponse> {
  return request<UserResponse>("/auth/register", {
    method: "POST",
    skipRefresh: true,
    body: JSON.stringify(payload),
  });
}

export function login(payload: LoginRequest): Promise<TokenPair> {
  return request<TokenPair>("/auth/login", {
    method: "POST",
    skipRefresh: true,
    body: JSON.stringify(payload),
  });
}

export function refresh(payload: RefreshRequest): Promise<AccessToken> {
  return request<AccessToken>("/auth/refresh", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function logout(payload: LogoutRequest): Promise<void> {
  return request<void>("/auth/logout", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function me(accessToken: string): Promise<UserResponse> {
  return request<UserResponse>("/auth/me", { accessToken });
}
