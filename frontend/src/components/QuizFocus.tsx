import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ArrowRight, CheckCircle2, X } from "lucide-react";

import { answerQuiz, startQuiz } from "../api/quiz";
import type { QuizDifficulty, QuizMode } from "../types";

const difficultyStyles: Record<QuizDifficulty, string> = {
  easy: "bg-emerald-100 text-emerald-700",
  medium: "bg-amber-100 text-amber-700",
  hard: "bg-rose-100 text-rose-700",
};

export function QuizFocus({ videoId, onClose }: { videoId: string; onClose: () => void }) {
  const [mode, setMode] = useState<QuizMode>("quick_drill");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [concept, setConcept] = useState("");
  const [difficulty, setDifficulty] = useState<QuizDifficulty>("easy");
  const [answer, setAnswer] = useState("");
  const [validation, setValidation] = useState("");
  const [feedback, setFeedback] = useState<{ score: number; text: string } | null>(null);
  const [answered, setAnswered] = useState(0);
  const [complete, setComplete] = useState(false);

  const startMutation = useMutation({
    mutationFn: () => startQuiz({ video_id: videoId, mode }),
    onSuccess(data) {
      setSessionId(data.session_id);
      setQuestion(data.first_question);
      setConcept(data.current_concept);
      setDifficulty(data.difficulty);
    },
  });

  const answerMutation = useMutation({
    mutationFn: (value: string) => answerQuiz({ session_id: sessionId!, answer: value }),
    onSuccess(data) {
      setAnswered((count) => count + 1);
      setFeedback({ score: data.score, text: data.feedback });
      setComplete(data.session_complete);
      if (data.next_question) setQuestion(data.next_question);
      if (data.current_concept) setConcept(data.current_concept);
      setDifficulty(data.difficulty);
      setAnswer("");
    },
  });

  function submitAnswer() {
    if (!answer.trim()) {
      setValidation("Please enter an answer");
      return;
    }
    setValidation("");
    answerMutation.mutate(answer.trim());
  }

  return (
    <div className="fixed inset-0 z-[70] overflow-y-auto bg-slate-950 text-white">
      <div className="mx-auto flex min-h-screen max-w-4xl flex-col px-5 py-6 sm:px-10">
        <header className="flex items-center justify-between">
          <span className="text-sm font-bold tracking-wide text-blue-300">COURSEFLOW FOCUS</span>
          <button onClick={onClose} className="rounded-xl bg-white/10 p-2 hover:bg-white/15" aria-label="Close quiz"><X /></button>
        </header>

        {!sessionId ? (
          <div className="m-auto w-full max-w-xl text-center">
            <p className="text-sm font-semibold text-blue-300">Ready when you are</p>
            <h1 className="mt-3 text-4xl font-bold">Choose your quiz pace</h1>
            <div className="mt-8 grid gap-3 sm:grid-cols-3">
              {(["quick_drill", "full_review", "weak_spot"] as QuizMode[]).map((value) => (
                <button key={value} onClick={() => setMode(value)} className={`rounded-2xl border px-4 py-5 text-sm font-semibold capitalize ${mode === value ? "border-blue-400 bg-blue-500/20" : "border-white/15 bg-white/5"}`}>
                  {value.replace("_", " ")}
                </button>
              ))}
            </div>
            <button onClick={() => startMutation.mutate()} disabled={startMutation.isPending} className="mt-7 rounded-xl bg-blue-500 px-7 py-3 font-semibold hover:bg-blue-400 disabled:opacity-50">
              {startMutation.isPending ? "Preparing..." : "Start quiz"}
            </button>
            {startMutation.error ? <p className="mt-4 text-sm text-rose-300">{startMutation.error.message}</p> : null}
          </div>
        ) : complete ? (
          <div className="m-auto max-w-xl text-center">
            <CheckCircle2 className="mx-auto h-16 w-16 text-emerald-400" />
            <h1 className="mt-5 text-4xl font-bold">Session complete</h1>
            <p className="mt-3 text-slate-300">You answered {answered} questions. Your results and weak concepts are saved for spaced repetition.</p>
            {feedback ? <p className="mt-6 text-2xl font-bold">{Math.round(feedback.score * 100)}% on the final answer</p> : null}
            <button onClick={onClose} className="mt-8 rounded-xl bg-white px-6 py-3 font-semibold text-slate-950">Back to notes</button>
          </div>
        ) : (
          <div className="m-auto w-full max-w-2xl py-12">
            <div className="flex items-center justify-between text-sm">
              <span className={`rounded-full px-3 py-1 font-semibold capitalize ${difficultyStyles[difficulty]}`}>{difficulty}</span>
              <span className="text-slate-400">{answered} answered</span>
            </div>
            <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-white/10">
              <div className="h-full bg-blue-400 transition-all" style={{ width: `${Math.min(100, answered * 20)}%` }} />
            </div>
            <p className="mt-10 text-sm font-semibold text-blue-300">{concept}</p>
            <h1 className="mt-3 text-3xl font-semibold leading-tight">{question}</h1>
            {feedback ? (
              <div className="mt-6 animate-score rounded-2xl border border-white/10 bg-white/5 p-5">
                <p className="text-2xl font-bold text-blue-300">{Math.round(feedback.score * 100)}%</p>
                <p className="mt-2 text-sm leading-6 text-slate-300">{feedback.text}</p>
              </div>
            ) : null}
            <textarea
              value={answer}
              onChange={(event) => setAnswer(event.target.value)}
              onKeyDown={(event) => {
                if (event.ctrlKey && event.key === "Enter") submitAnswer();
              }}
              rows={6}
              placeholder="Explain it in your own words..."
              className="mt-7 w-full rounded-2xl border border-white/15 bg-white/5 p-5 text-white outline-none placeholder:text-slate-500 focus:border-blue-400"
            />
            {validation ? <p className="mt-2 text-sm text-rose-300">{validation}</p> : null}
            <button onClick={submitAnswer} disabled={answerMutation.isPending} className="mt-4 flex items-center gap-2 rounded-xl bg-blue-500 px-6 py-3 font-semibold hover:bg-blue-400 disabled:opacity-50">
              {answerMutation.isPending ? "Checking..." : "Submit answer"} <ArrowRight className="h-4 w-4" />
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
