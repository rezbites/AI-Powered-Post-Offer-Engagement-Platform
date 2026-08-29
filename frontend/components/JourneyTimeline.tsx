"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { CandidateDetail } from "@/types/api";

/**
 * The engagement journey.
 *
 * Shows completed *and* pending steps together, as the brief requires. Pending
 * is a real state stored as a row rather than an absence of data, which is what
 * makes stage drop-off measurable in analytics.
 */
export function JourneyTimeline({ candidate }: { candidate: CandidateDetail }) {
  const qc = useQueryClient();

  const toggle = useMutation({
    mutationFn: ({ key, status }: { key: string; status: string }) =>
      api.completeStage(candidate.id, key, status),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["candidate", candidate.id] });
      qc.invalidateQueries({ queryKey: ["attention-queue"] });
    },
  });

  return (
    <section className="rounded-lg border border-slate-200 bg-white">
      <h2 className="border-b border-slate-200 px-5 py-3 text-sm font-semibold text-slate-900">
        Engagement journey
        <span className="ml-2 font-normal text-slate-500">
          {candidate.journey.completed} of {candidate.journey.total} complete
        </span>
      </h2>

      <ol className="px-5 py-4">
        {candidate.stages.map((s, i) => {
          const done = s.status === "completed";
          return (
            <li key={s.key} className="flex gap-3 pb-4 last:pb-0">
              <div className="flex flex-col items-center">
                <span
                  className={`flex h-6 w-6 items-center justify-center rounded-full border text-xs ${
                    done
                      ? "border-emerald-500 bg-emerald-500 text-white"
                      : s.is_overdue
                        ? "border-amber-500 bg-amber-50 text-amber-700"
                        : "border-slate-300 bg-white text-slate-400"
                  }`}
                >
                  {done ? "✓" : i + 1}
                </span>
                {i < candidate.stages.length - 1 && (
                  <span className="mt-1 h-full w-px flex-1 bg-slate-200" />
                )}
              </div>

              <div className="flex-1 pb-1">
                <div className="flex items-center justify-between gap-2">
                  <span
                    className={`text-sm ${done ? "text-slate-500 line-through" : "font-medium text-slate-900"}`}
                  >
                    {s.label}
                  </span>
                  <button
                    onClick={() =>
                      toggle.mutate({
                        key: s.key,
                        status: done ? "pending" : "completed",
                      })
                    }
                    className="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-600 hover:bg-slate-50"
                  >
                    {done ? "Reset" : "Mark done"}
                  </button>
                </div>
                <div className="text-xs text-slate-500">
                  {s.is_overdue && (
                    <span className="mr-2 font-medium text-amber-700">Overdue</span>
                  )}
                  {done
                    ? s.completed_at &&
                      `completed ${new Date(s.completed_at).toLocaleDateString("en-GB")}`
                    : s.due_date && `due ${s.due_date}`}
                </div>
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
