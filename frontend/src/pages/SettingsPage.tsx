import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { me } from "../api/auth";
import { getAccessToken } from "../api/client";
import { createEdgeToken, listEdgeTokens, revokeEdgeToken } from "../api/edge";
import { getExamPlan } from "../api/srs";

export function SettingsPage() {
  const [examDate, setExamDate] = useState("");
  const [newToken, setNewToken] = useState("");
  const queryClient = useQueryClient();
  const account = useQuery({ queryKey: ["me"], queryFn: () => me(getAccessToken() ?? "") });
  const plan = useMutation({ mutationFn: () => getExamPlan(examDate) });
  const tokens = useQuery({ queryKey: ["edge-tokens"], queryFn: listEdgeTokens });
  const createToken = useMutation({
    mutationFn: () => createEdgeToken("Windows transcript fetcher"),
    onSuccess(result) {
      setNewToken(result.token);
      void queryClient.invalidateQueries({ queryKey: ["edge-tokens"] });
    },
  });
  const revokeToken = useMutation({
    mutationFn: (tokenId: string) => revokeEdgeToken(tokenId),
    onSuccess() {
      void queryClient.invalidateQueries({ queryKey: ["edge-tokens"] });
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (examDate) plan.mutate();
  }

  return (
    <div className="max-w-3xl">
      <p className="eyebrow">Preferences</p>
      <h1 className="page-title">Settings</h1>
      <section className="mt-8 rounded-3xl border border-slate-200 bg-white p-7 shadow-sm">
        <h2 className="text-lg font-bold">Account</h2>
        <p className="mt-4 text-sm text-slate-500">Signed in as</p>
        <p className="mt-1 font-semibold">{account.data?.email ?? "Loading..."}</p>
      </section>
      <section className="mt-5 rounded-3xl border border-slate-200 bg-white p-7 shadow-sm">
        <h2 className="text-lg font-bold">Local transcript fetcher</h2>
        <p className="mt-2 text-sm leading-6 text-slate-500">
          Use a revocable device token for the Windows process that fetches YouTube metadata, captions, and fallback audio from your home IP.
        </p>
        <button onClick={() => createToken.mutate()} disabled={createToken.isPending} className="primary-button mt-5">
          {createToken.isPending ? "Creating..." : "Create fetcher token"}
        </button>
        {newToken ? (
          <div className="mt-5 rounded-2xl bg-amber-50 p-4 text-sm text-amber-900">
            <p className="font-bold">Copy this token now. It will not be shown again.</p>
            <code className="mt-3 block break-all rounded-xl bg-white p-3 text-xs">{newToken}</code>
            <p className="mt-3">Save it in <code>.env.edge-fetcher</code> as <code>COURSEFLOW_EDGE_TOKEN</code>.</p>
          </div>
        ) : null}
        <div className="mt-6 divide-y divide-slate-100">
          {tokens.data?.map((token) => (
            <div key={token.id} className="flex items-center justify-between gap-4 py-3 text-sm">
              <div>
                <p className="font-semibold">{token.name}</p>
                <p className="text-slate-500">
                  {token.token_prefix} · {token.revoked ? "revoked" : token.last_seen_at ? `last seen ${new Date(token.last_seen_at).toLocaleString()}` : "never used"}
                </p>
              </div>
              {!token.revoked ? (
                <button onClick={() => revokeToken.mutate(token.id)} className="rounded-xl px-3 py-2 font-semibold text-rose-600 hover:bg-rose-50">
                  Revoke
                </button>
              ) : null}
            </div>
          ))}
        </div>
        <div className="mt-5 rounded-2xl bg-slate-50 p-4 text-xs text-slate-600">
          <p className="font-semibold text-slate-800">Windows setup command</p>
          <code className="mt-2 block break-all">powershell -ExecutionPolicy Bypass -File .\local-fetcher\install-task.ps1</code>
        </div>
      </section>
      <section className="mt-5 rounded-3xl border border-slate-200 bg-white p-7 shadow-sm">
        <h2 className="text-lg font-bold">Exam study plan</h2>
        <p className="mt-2 text-sm leading-6 text-slate-500">Choose an exam date to calculate a backend-generated review plan. The date is not stored in the browser.</p>
        <form onSubmit={submit} className="mt-5 flex flex-col gap-3 sm:flex-row">
          <input type="date" required value={examDate} onChange={(event) => setExamDate(event.target.value)} className="rounded-xl border border-slate-200 px-4 py-3 outline-none focus:border-blue-500" />
          <button className="primary-button">Generate plan</button>
        </form>
        {plan.data ? (
          <div className="mt-6 rounded-2xl bg-blue-50 p-5 text-sm text-blue-900">
            <p className="font-bold">{plan.data.can_complete ? "Plan is achievable" : "Plan needs adjustment"}</p>
            <p className="mt-2 leading-6">{plan.data.message}</p>
            <p className="mt-2">{plan.data.total_cards} cards across {plan.data.days_remaining + 1} study days.</p>
          </div>
        ) : null}
        {plan.error ? <p className="mt-4 text-sm text-rose-600">{plan.error.message}</p> : null}
      </section>
    </div>
  );
}
