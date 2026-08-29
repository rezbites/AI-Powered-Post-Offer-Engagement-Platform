"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { CandidateDetail } from "@/types/api";

/**
 * Conversation history, with logging.
 *
 * Logging an interaction is not an afterthought — it is what makes the rest of
 * the system work. Inbound messages are the raw material the AI reads for risk
 * signals, and `last_interaction_at` is what the silence rules and the
 * attention queue filter on. Without a way to record a conversation, a new
 * candidate can never be analysed and never leaves the "never contacted" state.
 *
 * Inbound and outbound are visually distinct because a recruiter checking a
 * quoted piece of evidence needs to find the candidate's own words fast.
 */

const CHANNELS = ["email", "whatsapp", "call", "note"] as const;

export function Conversation({ candidate }: { candidate: CandidateDetail }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [direction, setDirection] = useState<"inbound" | "outbound">("inbound");
  const [channel, setChannel] = useState<string>("email");
  const [content, setContent] = useState("");
  const [occurredAt, setOccurredAt] = useState("");

  const add = useMutation({
    mutationFn: () =>
      api.addInteraction(candidate.id, {
        channel,
        direction,
        content,
        // Blank means "now". Supplying a past timestamp is how history gets
        // back-filled; the API refuses to move last_interaction_at backwards
        // so an old import cannot make someone look freshly contacted.
        occurred_at: occurredAt ? new Date(occurredAt).toISOString() : undefined,
      }),
    onSuccess: () => {
      setContent("");
      setOccurredAt("");
      setOpen(false);
      qc.invalidateQueries({ queryKey: ["candidate", candidate.id] });
      qc.invalidateQueries({ queryKey: ["attention-queue"] });
      qc.invalidateQueries({ queryKey: ["candidates"] });
    },
  });

  const field =
    "rounded-md border border-slate-300 px-2.5 py-1.5 text-sm text-slate-900";

  return (
    <section className="rounded-lg border border-slate-200 bg-white">
      <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3">
        <h2 className="text-sm font-semibold text-slate-900">
          Conversation history
          <span className="ml-2 font-normal text-slate-500">
            {candidate.interactions.length} messages
          </span>
        </h2>
        <button
          onClick={() => setOpen((v) => !v)}
          className="rounded-md border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
        >
          {open ? "Cancel" : "+ Log interaction"}
        </button>
      </div>

      {open && (
        <div className="border-b border-slate-200 bg-slate-50 px-5 py-4">
          <div className="flex flex-wrap items-center gap-2">
            {/* Direction first: it is the field that changes what the AI does
                with this message, so it should not be buried. */}
            <div className="flex overflow-hidden rounded-md border border-slate-300">
              {(["inbound", "outbound"] as const).map((d) => (
                <button
                  key={d}
                  onClick={() => setDirection(d)}
                  className={`px-3 py-1.5 text-xs font-medium ${
                    direction === d
                      ? "bg-slate-900 text-white"
                      : "bg-white text-slate-600"
                  }`}
                >
                  {d === "inbound" ? "From candidate" : "From recruiter"}
                </button>
              ))}
            </div>

            <select
              className={field}
              value={channel}
              onChange={(e) => setChannel(e.target.value)}
            >
              {CHANNELS.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>

            <input
              className={field}
              type="datetime-local"
              value={occurredAt}
              onChange={(e) => setOccurredAt(e.target.value)}
              title="Leave blank for now"
            />
          </div>

          <textarea
            className="mt-2 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            rows={3}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder={
              direction === "inbound"
                ? "What did the candidate say? Their exact words feed risk detection."
                : "What did you send them?"
            }
          />

          <div className="mt-2 flex items-center justify-between">
            <p className="text-xs text-slate-500">
              {direction === "inbound"
                ? "Re-analyse afterwards to pick up any new signals."
                : "Logging this clears the silence flag."}
            </p>
            <button
              onClick={() => add.mutate()}
              disabled={!content.trim() || add.isPending}
              className="rounded-md bg-slate-900 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40"
            >
              {add.isPending ? "Saving…" : "Log interaction"}
            </button>
          </div>
        </div>
      )}

      {candidate.interactions.length === 0 ? (
        <p className="px-5 py-6 text-sm text-slate-500">
          No interactions recorded. Log one above — the AI reads the
          candidate&apos;s own words to detect concerns, so it has nothing to
          work with until then.
        </p>
      ) : (
        <ul className="divide-y divide-slate-100">
          {candidate.interactions.map((i) => {
            const inbound = i.direction === "inbound";
            return (
              <li
                key={i.id}
                className={`px-5 py-3 ${inbound ? "bg-blue-50/40" : ""}`}
              >
                <div className="flex items-center gap-2 text-xs">
                  <span
                    className={`rounded px-1.5 py-0.5 font-medium ${
                      inbound
                        ? "bg-blue-100 text-blue-800"
                        : "bg-slate-100 text-slate-600"
                    }`}
                  >
                    {inbound ? "Candidate" : "Recruiter"}
                  </span>
                  <span className="text-slate-400">{i.channel}</span>
                  <span className="ml-auto text-slate-400">
                    {new Date(i.occurred_at).toLocaleString("en-GB", {
                      day: "2-digit",
                      month: "short",
                      year: "numeric",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </span>
                </div>
                <p
                  className={`mt-1 whitespace-pre-wrap text-sm ${
                    inbound ? "font-medium text-slate-800" : "text-slate-600"
                  }`}
                >
                  {i.content}
                </p>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
