import type { VideoStatus } from "../types";

const styles: Record<VideoStatus | "partial", string> = {
  pending: "bg-slate-100 text-slate-700",
  deferred: "bg-violet-100 text-violet-700",
  rate_limited: "bg-cyan-100 text-cyan-700",
  batch_processing: "bg-indigo-100 text-indigo-700",
  processing: "bg-blue-100 text-blue-700",
  completed: "bg-emerald-100 text-emerald-700",
  failed: "bg-rose-100 text-rose-700",
  manual: "bg-amber-100 text-amber-700",
  partial: "bg-amber-100 text-amber-700",
};

export function StatusChip({ status }: { status: VideoStatus | "partial" }) {
  return (
    <span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-semibold capitalize ${styles[status]}`}>
      {status.replaceAll("_", " ")}
    </span>
  );
}
