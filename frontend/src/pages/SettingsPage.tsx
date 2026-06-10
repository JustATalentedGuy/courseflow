import { useState, type FormEvent } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { me } from "../api/auth";
import { getAccessToken } from "../api/client";
import { getExamPlan } from "../api/srs";

export function SettingsPage() {
  const [examDate, setExamDate] = useState("");
  const account = useQuery({ queryKey: ["me"], queryFn: () => me(getAccessToken() ?? "") });
  const plan = useMutation({ mutationFn: () => getExamPlan(examDate) });

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
