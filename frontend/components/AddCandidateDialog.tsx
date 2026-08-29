"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";

/**
 * Add a candidate.
 *
 * Creating a candidate also materialises their full engagement journey
 * server-side — a `candidate_stages` row per stage, with due dates frozen from
 * each stage's SLA. So a new candidate is immediately visible in the funnel and
 * eligible for the automation rules, rather than sitting in a half-created
 * state until someone touches them.
 *
 * Validation is deliberately duplicated: the browser catches obvious mistakes
 * for fast feedback, and the API re-validates because a client is not a trust
 * boundary. Server errors surface here verbatim rather than being swallowed.
 */
export function AddCandidateDialog() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const today = new Date().toISOString().slice(0, 10);
  const inThirty = new Date(Date.now() + 30 * 86_400_000)
    .toISOString()
    .slice(0, 10);

  const [form, setForm] = useState({
    name: "",
    email: "",
    phone: "",
    role_title: "",
    location: "",
    offer_date: today,
    joining_date: inThirty,
    recruiter_id: "",
    notes: "",
  });

  const { data: recruiters } = useQuery({
    queryKey: ["recruiters"],
    queryFn: api.recruiters,
    enabled: open,
  });

  const create = useMutation({
    mutationFn: () =>
      api.createCandidate({
        ...form,
        // Empty optional strings must become null, not "" — the API treats an
        // empty string as a supplied value and would store it.
        phone: form.phone.trim() || null,
        notes: form.notes.trim() || null,
        recruiter_id: form.recruiter_id || recruiters?.[0]?.id || "",
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["candidates"] });
      qc.invalidateQueries({ queryKey: ["attention-queue"] });
      qc.invalidateQueries({ queryKey: ["analytics"] });
      setOpen(false);
      setError(null);
      setForm({ ...form, name: "", email: "", phone: "", notes: "" });
    },
    onError: (e) =>
      setError(
        e instanceof ApiError ? e.message : "Could not create the candidate.",
      ),
  });

  const joiningBeforeOffer = form.joining_date < form.offer_date;
  const canSubmit =
    form.name.trim() &&
    form.email.trim() &&
    form.role_title.trim() &&
    form.location.trim() &&
    !joiningBeforeOffer &&
    !create.isPending;

  const field =
    "w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400";
  const label = "block text-xs font-medium text-slate-600 mb-1";

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-800"
      >
        + Add candidate
      </button>
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/40 p-6">
      <div className="w-full max-w-2xl rounded-lg border border-slate-200 bg-white shadow-lg">
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3">
          <h2 className="text-sm font-semibold text-slate-900">Add candidate</h2>
          <button
            onClick={() => setOpen(false)}
            className="text-sm text-slate-400 hover:text-slate-700"
          >
            Cancel
          </button>
        </div>

        <div className="grid gap-4 px-5 py-4 sm:grid-cols-2">
          <div>
            <label className={label}>Full name *</label>
            <input
              className={field}
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="Priya Sharma"
            />
          </div>
          <div>
            <label className={label}>Email *</label>
            <input
              className={field}
              type="email"
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
              placeholder="priya@example.com"
            />
          </div>
          <div>
            <label className={label}>Role *</label>
            <input
              className={field}
              value={form.role_title}
              onChange={(e) => setForm({ ...form, role_title: e.target.value })}
              placeholder="Software Engineer II"
            />
          </div>
          <div>
            <label className={label}>Location *</label>
            <input
              className={field}
              value={form.location}
              onChange={(e) => setForm({ ...form, location: e.target.value })}
              placeholder="Bengaluru"
            />
          </div>
          <div>
            <label className={label}>Offer date *</label>
            <input
              className={field}
              type="date"
              value={form.offer_date}
              onChange={(e) => setForm({ ...form, offer_date: e.target.value })}
            />
          </div>
          <div>
            <label className={label}>Joining date *</label>
            <input
              className={field}
              type="date"
              value={form.joining_date}
              onChange={(e) =>
                setForm({ ...form, joining_date: e.target.value })
              }
            />
            {/* Caught here for immediate feedback, and again server-side —
                every risk and analytics figure derives from this window. */}
            {joiningBeforeOffer && (
              <p className="mt-1 text-xs text-red-600">
                Joining date must be on or after the offer date.
              </p>
            )}
          </div>
          <div>
            <label className={label}>Phone</label>
            <input
              className={field}
              value={form.phone}
              onChange={(e) => setForm({ ...form, phone: e.target.value })}
              placeholder="+91…"
            />
          </div>
          <div>
            <label className={label}>Assigned recruiter</label>
            <select
              className={field}
              value={form.recruiter_id}
              onChange={(e) =>
                setForm({ ...form, recruiter_id: e.target.value })
              }
            >
              {(recruiters ?? []).map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </select>
          </div>
          <div className="sm:col-span-2">
            <label className={label}>Notes</label>
            <textarea
              className={field}
              rows={2}
              value={form.notes}
              onChange={(e) => setForm({ ...form, notes: e.target.value })}
              placeholder="Anything the team should know"
            />
          </div>
        </div>

        {error && (
          <p className="mx-5 mb-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
            {error}
          </p>
        )}

        <div className="flex items-center justify-between border-t border-slate-200 px-5 py-3">
          <p className="text-xs text-slate-500">
            The full engagement journey is created automatically.
          </p>
          <button
            onClick={() => create.mutate()}
            disabled={!canSubmit}
            className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
          >
            {create.isPending ? "Creating…" : "Create candidate"}
          </button>
        </div>
      </div>
    </div>
  );
}
