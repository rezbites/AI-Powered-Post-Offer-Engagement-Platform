"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { RiskBadge } from "./RiskBadge";

/**
 * "Who needs my attention today?"
 *
 * This is the first thing a recruiter sees, and it is the reason the product
 * exists. A table of sixty candidates answers "what data do we hold"; this
 * answers "what should I do this morning", which is a different question.
 *
 * Ordering is deterministic and computed server-side by a pure ranking
 * function - not an LLM call. A queue that reshuffles between refreshes is one
 * recruiters stop trusting.
 */
export function AttentionQueue() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["attention-queue"],
    queryFn: () => api.attentionQueue(6),
  });

  if (isLoading) {
    return (
      <section className="rounded-lg border border-slate-200 bg-white p-6">
        <div className="h-5 w-48 animate-pulse rounded bg-slate-200" />
      </section>
    );
  }

  if (error) {
    return (
      <section className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800">
        Could not load the attention queue. Is the API running on port 8000?
      </section>
    );
  }

  if (!data || data.items.length === 0) {
    return (
      <section className="rounded-lg border border-emerald-200 bg-emerald-50 p-6">
        <h2 className="text-sm font-semibold text-emerald-900">
          Nothing needs attention today
        </h2>
        <p className="mt-1 text-sm text-emerald-800">
          Every active candidate has been contacted recently and is on track.
        </p>
      </section>
    );
  }

  return (
    <section className="rounded-lg border border-slate-200 bg-white">
      <div className="flex items-baseline justify-between border-b border-slate-200 px-5 py-3">
        <h2 className="text-sm font-semibold text-slate-900">
          Requires attention
          <span className="ml-2 font-normal text-slate-500">
            {data.items.length} of {data.total_active} active candidates
          </span>
        </h2>
        <span className="text-xs text-slate-400">
          as of {data.generated_for}
        </span>
      </div>

      <ol className="divide-y divide-slate-100">
        {data.items.map((item, index) => (
          <li key={item.candidate_id} className="px-5 py-4">
            <div className="flex items-start gap-4">
              <span className="mt-0.5 w-4 text-sm font-medium tabular-nums text-slate-400">
                {index + 1}
              </span>

              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <Link
                    href={`/candidates/${item.candidate_id}`}
                    className="text-sm font-semibold text-slate-900 hover:underline"
                  >
                    {item.name}
                  </Link>
                  <RiskBadge
                    level={item.risk_level}
                    confidence={item.risk_confidence}
                    size="sm"
                  />
                  <span className="text-xs text-slate-500">
                    {item.days_to_joining < 0
                      ? `joining date passed ${Math.abs(item.days_to_joining)}d ago`
                      : `joining in ${item.days_to_joining} days`}
                  </span>
                </div>

                <p className="mt-1 text-sm text-slate-600">
                  {/* The "why" is on the row itself. A queue entry a recruiter
                      cannot justify is one they will ignore. */}
                  {item.reasons.join(" · ")}
                </p>

                <p className="mt-1.5 text-sm font-medium text-slate-900">
                  → {item.recommended_action_label}
                </p>
              </div>

              <Link
                href={`/candidates/${item.candidate_id}`}
                className="shrink-0 rounded-md border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
              >
                Open
              </Link>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
