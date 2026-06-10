import { createContext, useContext, useMemo, useState, type PropsWithChildren } from "react";

import { clearTokens, getAccessToken, setTokens } from "../api/client";

interface AuthContextValue {
  isAuthenticated: boolean;
  signIn: (accessToken: string, refreshToken: string) => void;
  signOut: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: PropsWithChildren) {
  const [accessToken, setAccessToken] = useState(getAccessToken);

  const value = useMemo<AuthContextValue>(
    () => ({
      isAuthenticated: Boolean(accessToken),
      signIn(nextAccessToken, refreshToken) {
        setTokens(nextAccessToken, refreshToken);
        setAccessToken(nextAccessToken);
      },
      signOut() {
        clearTokens();
        setAccessToken(null);
      },
    }),
    [accessToken],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within AuthProvider");
  return context;
}
