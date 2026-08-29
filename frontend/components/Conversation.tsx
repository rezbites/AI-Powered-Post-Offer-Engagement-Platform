"use client";

import type { CandidateDetail } from "@/types/api";

/**
 * Conversation history.
 *
 * Inbound messages are visually distinct because they are the raw material the
 * AI reads for risk signals - a recruiter checking a quoted piece of evidence
 * needs to find it quickly.
 */
export function Conversation({ candidate }: { candidate: CandidateDetail }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white">
      <h2 className="border-b border-slate-200 px-5 py-3 text-sm font-semibold text-slate-900">
        Conversation history
        <span className="ml-2 font-normal text-slate-500">
          {candidate.interactions.length} messages
        </span>
      </h2>

      {candidate.interactions.length === 0 ? (
        <p className="px-5 py-6 text-sm text-slate-500">
          No interactions recorded. This candidate has never been contacted.
        </p>
      ) : (
        <ul className="divide-y divide-slate-100">
          {candidate.interactions.map((i) => {
            const inbound = i.direction === "inbound";
            return (
              <li key={i.id} className="px-5 py-3">
                <div className="flex items-center gap-2 text-xs">
                  <span
                    className={`rounded px-1.5 py-0.5 font-medium ${
                      inbound
                        ? "bg-blue-50 text-blue-700"
                        : "bg-slate-100 text-slate-600"
                    }`}
                  >
                    {inbound ? "Candidate" : "Recruiter"}
                  </span>
                  <span className="text-slate-400">{i.channel}</span>
                  <span className="text-slate-400">
                    {new Date(i.occurred_at).toLocaleDateString("en-GB")}
                  </span>
                </div>
                <p className="mt-1 text-sm text-slate-700">{i.content}</p>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
