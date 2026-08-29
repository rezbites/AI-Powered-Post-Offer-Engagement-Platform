"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { CandidateDetail, CandidateStatus } from "@/types/api";

/**
 * Recruiter notes and engagement status.
 *
 * Deliberately separate from AI output. These are a human's own record - what
 * they were told on a call, context the transcript does not carry - and folding
 * them into the AI summary would blur who said what. The AI never writes here.
 */

const STATUSES: { value: CandidateStatus; label: string }[] = [
  { value: "offer_accepted", label: "Offer accepted" },
  { value: "engaged", label: "Engaged" },
  { value: "at_risk", label: "At risk" },
  { value: "joined", label: "Joined" },
  { value: "dropped_out", label: "Dropped out" },
];

export function NotesPanel({ candidate }: { candidate: CandidateDetail }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [notes, setNotes] = useState(candidate.notes ?? "");
  const [status, setStatus] = useState<CandidateStatus>(candidate.status);

  const save = useMutation({
    mutationFn: () => api.updateCandidate(candidate.id, { notes, status }),
    onSuccess: () => {
      setEditing(false);
      qc.invalidateQueries({ queryKey: ["candidate", candidate.id] });
      qc.invalidateQueries({ queryKey: ["candidates"] });
      // Marking someone joined or dropped out changes the conversion
      // denominator, so stale analytics tiles would immediately disagree.
      qc.invalidateQueries({ queryKey: ["analytics"] });
      qc.invalidateQueries({ queryKey: ["attention-queue"] });
    },
  });

  return (
    <section className="rounded-lg border border-slate-200 bg-white">
      <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3">
        <h2 className="text-sm font-semibold text-slate-900">
          Recruiter notes &amp; status
        </h2>
        <button
          onClick={() => {
            setNotes(candidate.notes ?? "");
            setStatus(candidate.status);
            setEditing((v) => !v);
          }}
          className="rounded-md border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
        >
          {editing ? "Cancel" : "Edit"}
        </button>
      </div>

      <div className="px-5 py-4">
        {editing ? (
          <>
            <label className="mb-1 block text-xs font-medium text-slate-600">
              Engagement status
            </label>
            <select
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
              value={status}
              onChange={(e) => setStatus(e.target.value as CandidateStatus)}
            >
              {STATUSES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
            <p className="mt-1 text-xs text-slate-500">
              Joined and dropped out are terminal - they are what makes
              offer-to-join conversion computable.
            </p>

            <label className="mb-1 mt-3 block text-xs font-medium text-slate-600">
              Notes
            </label>
            <textarea
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
              rows={4}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Context the conversation history does not capture."
            />

            <button
              onClick={() => save.mutate()}
              disabled={save.isPending}
              className="mt-3 rounded-md bg-slate-900 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40"
            >
              {save.isPending ? "Saving…" : "Save"}
            </button>
          </>
        ) : (
          <>
            <div className="flex items-center gap-2">
              <span className="text-xs text-slate-500">Status</span>
              <span className="rounded bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700">
                {STATUSES.find((s) => s.value === candidate.status)?.label ??
                  candidate.status}
              </span>
            </div>
            <p className="mt-3 whitespace-pre-wrap text-sm text-slate-700">
              {candidate.notes || (
                <span className="text-slate-400">
                  No notes yet. Add context the conversation history does not
                  capture.
                </span>
              )}
            </p>
          </>
        )}
      </div>
    </section>
  );
}
