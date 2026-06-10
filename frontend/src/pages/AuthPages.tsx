import { useState, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { BookOpen } from "lucide-react";
import { Link, Navigate, useNavigate } from "react-router-dom";

import { login, register } from "../api/auth";
import { useAuth } from "../auth/AuthContext";

function AuthFrame({ kind }: { kind: "login" | "register" }) {
  const navigate = useNavigate();
  const { isAuthenticated, signIn } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const mutation = useMutation({
    mutationFn: async () => {
      if (kind === "register") await register({ email, password });
      return login({ email, password });
    },
    onSuccess(tokens) {
      signIn(tokens.access_token, tokens.refresh_token);
      navigate("/dashboard");
    },
  });

  if (isAuthenticated) return <Navigate to="/dashboard" replace />;

  function submit(event: FormEvent) {
    event.preventDefault();
    mutation.mutate();
  }

  return (
    <main className="grid min-h-screen bg-slate-950 lg:grid-cols-2">
      <section className="hidden flex-col justify-between bg-gradient-to-br from-blue-600 to-indigo-800 p-12 text-white lg:flex">
        <div className="flex items-center gap-3 text-xl font-bold"><BookOpen /> CourseFlow</div>
        <div>
          <p className="text-sm font-bold uppercase tracking-[0.2em] text-blue-200">Learn actively</p>
          <h1 className="mt-4 max-w-lg text-5xl font-bold leading-tight">Turn every course into knowledge that sticks.</h1>
          <p className="mt-5 max-w-md leading-7 text-blue-100">Structured notes, adaptive quizzes, semantic search, and spaced repetition in one calm workspace.</p>
        </div>
        <p className="text-sm text-blue-200">Your YouTube learning library, organized.</p>
      </section>
      <section className="grid place-items-center px-5 py-12">
        <form onSubmit={submit} className="w-full max-w-md rounded-3xl bg-white p-7 shadow-2xl sm:p-9">
          <div className="flex items-center gap-3 text-xl font-bold text-slate-950 lg:hidden"><BookOpen className="text-blue-600" /> CourseFlow</div>
          <h1 className="mt-8 text-3xl font-bold text-slate-950">{kind === "login" ? "Welcome back" : "Create your account"}</h1>
          <p className="mt-2 text-sm text-slate-500">{kind === "login" ? "Continue where you left off." : "Start building your active learning library."}</p>
          <label className="mt-7 block text-sm font-semibold text-slate-700">Email</label>
          <input type="email" required value={email} onChange={(event) => setEmail(event.target.value)} className="mt-2 w-full rounded-xl border border-slate-200 px-4 py-3 outline-none focus:border-blue-500" />
          <label className="mt-5 block text-sm font-semibold text-slate-700">Password</label>
          <input type="password" required minLength={kind === "register" ? 8 : undefined} value={password} onChange={(event) => setPassword(event.target.value)} className="mt-2 w-full rounded-xl border border-slate-200 px-4 py-3 outline-none focus:border-blue-500" />
          {mutation.error ? <p className="mt-4 text-sm text-rose-600">{mutation.error.message}</p> : null}
          <button disabled={mutation.isPending} className="mt-7 w-full rounded-xl bg-blue-600 px-4 py-3 font-semibold text-white hover:bg-blue-500 disabled:opacity-50">
            {mutation.isPending ? "Please wait..." : kind === "login" ? "Sign in" : "Create account"}
          </button>
          <p className="mt-6 text-center text-sm text-slate-500">
            {kind === "login" ? "New to CourseFlow? " : "Already have an account? "}
            <Link className="font-semibold text-blue-600" to={kind === "login" ? "/register" : "/login"}>{kind === "login" ? "Register" : "Sign in"}</Link>
          </p>
        </form>
      </section>
    </main>
  );
}

export function LoginPage() {
  return <AuthFrame kind="login" />;
}

export function RegisterPage() {
  return <AuthFrame kind="register" />;
}
