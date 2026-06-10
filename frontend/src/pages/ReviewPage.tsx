import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2 } from "lucide-react";

import { getDueCards, reviewCard } from "../api/srs";
import { EmptyState } from "../components/EmptyState";

const ratings = [
  { label: "Again", score: 0.2, style: "bg-rose-100 text-rose-700" },
  { label: "Hard", score: 0.5, style: "bg-amber-100 text-amber-700" },
  { label: "Good", score: 0.75, style: "bg-blue-100 text-blue-700" },
  { label: "Easy", score: 1, style: "bg-emerald-100 text-emerald-700" },
];

export function ReviewPage() {
  const queryClient = useQueryClient();
  const [index, setIndex] = useState(0);
  const cards = useQuery({ queryKey: ["due-cards"], queryFn: getDueCards });
  const mutation = useMutation({
    mutationFn: ({ cardId, score }: { cardId: string; score: number }) => reviewCard(cardId, score),
    onSuccess() {
      setIndex((value) => value + 1);
      void queryClient.invalidateQueries({ queryKey: ["srs-stats"] });
    },
  });
  const card = cards.data?.[index];

  return (
    <div className="mx-auto max-w-3xl">
      <p className="eyebrow">Spaced repetition</p>
      <h1 className="page-title">Today's review</h1>
      {card ? (
        <div className="mt-8 rounded-3xl border border-slate-200 bg-white p-8 text-center shadow-sm sm:p-12">
          <p className="text-sm font-semibold text-slate-400">Card {index + 1} of {cards.data?.length}</p>
          <h2 className="mt-8 text-3xl font-bold">{card.concept}</h2>
          <p className="mt-4 text-sm text-slate-500">How well can you explain this concept without looking at your notes?</p>
          <div className="mt-10 grid grid-cols-2 gap-3 sm:grid-cols-4">
            {ratings.map((rating) => (
              <button key={rating.label} disabled={mutation.isPending} onClick={() => mutation.mutate({ cardId: card.id, score: rating.score })} className={`rounded-xl px-4 py-3 text-sm font-bold ${rating.style}`}>
                {rating.label}
              </button>
            ))}
          </div>
        </div>
      ) : cards.isLoading ? <div className="mt-8 h-72 animate-pulse rounded-3xl bg-slate-200" /> : (
        <div className="mt-8"><EmptyState title="Review complete" description="You are caught up for today. New cards will appear as they become due." action={<CheckCircle2 className="mx-auto h-10 w-10 text-emerald-500" />} /></div>
      )}
    </div>
  );
}
