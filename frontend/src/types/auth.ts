export interface RegisterRequest {
  email: string;
  password: string;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface RefreshRequest {
  refresh_token: string;
}

export interface LogoutRequest {
  refresh_token: string;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: "bearer";
}

export interface AccessToken {
  access_token: string;
}

export interface UserResponse {
  user_id: string;
  email: string;
  created_at: string | null;
}
