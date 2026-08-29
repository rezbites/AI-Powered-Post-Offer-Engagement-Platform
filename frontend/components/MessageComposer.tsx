"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { ProviderToggle, useDefaultProvider } from "./ProviderToggle";
import type { GeneratedMessage } from "@/types/api";

/**
 * AI message drafting, with the human approval gate.
 *
 * Nothing here reaches a candidate automatically. A draft stays a draft until a
 * recruiter reads it and approves it, and that gate is the real defence against
 * prompt injection: text injected into a candidate message cannot approve
 * itself. Sending is simulated, as the brief permits, but the approval is
 * genuine - recorded, audited, and required.
 */
export function MessageComposer({ candidateId }: { candidateId: string }) {
  const qc = useQueryClient();
  const [warnings, setWarnings] = useState<string[]>([]);
  // Which draft is being edited, and its working copy.
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftSubject, setDraftSubject] = useState<string | null>(null);
  const [draftBody, setDraftBody] = useState("");
  // Starts on whatever the server actually resolved. The previous
  // hardcoded default went stale the moment a provider was swapped out,
  // leaving the panel pre-selected on one with no key.
  const defaultProvider = useDefaultProvider();
  const [provider, setProvider] = useState<string | null>(null);
  const activeProvider = provider ?? defaultProvider;

  const { data: messages } = useQuery({
    queryKey: ["messages", candidateId],
    queryFn: () => api.messages(candidateId),
  });

  const draft = useMutation({
    mutationFn: (channel: "email" | "whatsapp") =>
      api.draftMessage(candidateId, channel, activeProvider),
    onSuccess: (msg: GeneratedMessage) => {
      setWarnings(msg.warnings ?? []);
      qc.invalidateQueries({ queryKey: ["messages", candidateId] });
    },
  });

  const save = useMutation({
    mutationFn: () => api.editMessage(editingId!, draftSubject, draftBody),
    onSuccess: () => {
      setEditingId(null);
      qc.invalidateQueries({ queryKey: ["messages", candidateId] });
    },
  });

  const approve = useMutation({
    mutationFn: (id: string) => api.approveMessage(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["messages", candidateId] }),
  });

  return (
    <section className="rounded-lg border border-slate-200 bg-white">
      <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3">
        <h2 className="text-sm font-semibold text-slate-900">Messages</h2>
        <div className="flex flex-wrap items-center gap-2">
          <ProviderToggle value={activeProvider} onChange={setProvider} />
          <button
            onClick={() => draft.mutate("email")}
            disabled={draft.isPending}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            Draft email
          </button>
          <button
            onClick={() => draft.mutate("whatsapp")}
            disabled={draft.isPending}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            Draft WhatsApp
          </button>
        </div>
      </div>

      {draft.isError && (
        <p className="border-b border-amber-200 bg-amber-50 px-5 py-2 text-sm text-amber-800">
          {/* No templated fallback here on purpose: a generic message dressed up
              as personalised is worse than asking the recruiter to write it. */}
          Message generation is unavailable. Please write this one manually.
        </p>
      )}

      {warnings.length > 0 && (
        <div className="border-b border-amber-200 bg-amber-50 px-5 py-2">
          {warnings.map((w) => (
            <p key={w} className="text-xs text-amber-800">
              ⚠ {w}
            </p>
          ))}
        </div>
      )}

      {!messages?.length ? (
        <p className="px-5 py-6 text-sm text-slate-500">
          No drafts yet. Generate one above — it will be saved as a draft for
          your review before anything is sent.
        </p>
      ) : (
        <ul className="divide-y divide-slate-100">
          {messages.map((m) => (
            <li key={m.id} className="px-5 py-4">
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-600">
                  {m.channel}
                </span>
                <span
                  className={`rounded px-1.5 py-0.5 font-medium ${
                    m.status === "draft"
                      ? "bg-slate-100 text-slate-700"
                      : "bg-emerald-50 text-emerald-700"
                  }`}
                >
                  {m.status === "sent_simulated" ? "sent (simulated)" : m.status}
                </span>
                {m.mode === "demo" && (
                  <span className="rounded bg-amber-50 px-1.5 py-0.5 text-amber-800">
                    mock fixture
                  </span>
                )}
                {m.tone === "human_edited" && (
                  <span className="rounded bg-blue-50 px-1.5 py-0.5 text-blue-800">
                    edited by you
                  </span>
                )}
                <span className="text-slate-400">
                  {new Date(m.created_at).toLocaleString("en-GB")}
                </span>
              </div>

              {editingId === m.id ? (
                <div className="mt-2">
                  {/* Editable before approval. A recruiter who cannot adjust
                      the wording pastes it into their own mail client, and the
                      approval trail disappears entirely. */}
                  {m.channel === "email" && (
                    <input
                      className="mb-2 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                      value={draftSubject ?? ""}
                      onChange={(e) => setDraftSubject(e.target.value)}
                      placeholder="Subject"
                    />
                  )}
                  <textarea
                    className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                    rows={8}
                    value={draftBody}
                    onChange={(e) => setDraftBody(e.target.value)}
                  />
                  <div className="mt-2 flex gap-2">
                    <button
                      onClick={() => save.mutate()}
                      disabled={!draftBody.trim() || save.isPending}
                      className="rounded-md bg-slate-900 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40"
                    >
                      {save.isPending ? "Saving…" : "Save changes"}
                    </button>
                    <button
                      onClick={() => setEditingId(null)}
                      className="rounded-md border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  {m.subject && (
                    <p className="mt-2 text-sm font-medium text-slate-900">
                      {m.subject}
                    </p>
                  )}
                  <p className="mt-1 whitespace-pre-wrap text-sm text-slate-700">
                    {m.body}
                  </p>

                  {m.status === "draft" && (
                    <div className="mt-3 flex gap-2">
                      <button
                        onClick={() => {
                          setEditingId(m.id);
                          setDraftSubject(m.subject);
                          setDraftBody(m.body);
                        }}
                        className="rounded-md border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => approve.mutate(m.id)}
                        disabled={approve.isPending}
                        className="rounded-md bg-slate-900 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
                      >
                        Approve &amp; send (simulated)
                      </button>
                    </div>
                  )}
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
