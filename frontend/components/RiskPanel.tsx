"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { ProviderToggle } from "./ProviderToggle";
import { RiskBadge } from "./RiskBadge";
import type { CandidateDetail, RiskLevel } from "@/types/api";

/**
 * The explainability panel.
 *
 * Risk is never shown as a bare label. Three things always travel with it:
 *
 * - **Why** - the contributing factors, so a recruiter can disagree with the
 *   reasoning rather than just the verdict.
 * - **Provenance** - AI, rule, or a named colleague's override. Presenting a
 *   human decision as a model output (or vice versa) would be misleading.
 * - **Confidence** - labelled "heuristic" because it is a derived ordinal, not
 *   a calibrated probability. There is no outcome data to calibrate against,
 *   and dressing it up as a percentage-certain would be a false claim.
 */
export function RiskPanel({ candidate }: { candidate: CandidateDetail }) {
  const qc = useQueryClient();
  const [overriding, setOverriding] = useState(false);
  const [level, setLevel] = useState<RiskLevel>(candidate.risk.level);
  const [reason, setReason] = useState("");
  const [provider, setProvider] = useState("gemini");

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["candidate", candidate.id] });
    qc.invalidateQueries({ queryKey: ["attention-queue"] });
    qc.invalidateQueries({ queryKey: ["candidates"] });
  };

  const override = useMutation({
    mutationFn: () => api.overrideRisk(candidate.id, level, reason),
    onSuccess: () => {
      setOverriding(false);
      setReason("");
      invalidate();
    },
  });

  const revert = useMutation({
    mutationFn: () => api.revertRisk(candidate.id),
    onSuccess: invalidate,
  });

  const analyze = useMutation({
    mutationFn: () => api.analyze(candidate.id, true, provider),
    onSuccess: invalidate,
  });

  const risk = candidate.risk;
  const isHuman = risk.source === "human";

  return (
    <section className="rounded-lg border border-slate-200 bg-white">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-5 py-4">
        <div className="flex items-center gap-3">
          <RiskBadge
            level={risk.level}
            confidence={risk.confidence > 0 ? risk.confidence : undefined}
          />
          {/* 0.0 means "nothing has assessed this candidate yet", which is a
              different statement from "assessed, with zero confidence".
              Rendering both as 0% would misrepresent the first. */}
          <span className="text-xs text-slate-500">
            {risk.confidence > 0
              ? `${Math.round(risk.confidence * 100)}% confidence (heuristic)`
              : "not yet assessed"}
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <ProviderToggle value={provider} onChange={setProvider} />
          <button
            onClick={() => analyze.mutate()}
            disabled={analyze.isPending}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            {analyze.isPending ? "Analysing…" : "Re-analyse"}
          </button>
          {isHuman ? (
            <button
              onClick={() => revert.mutate()}
              disabled={revert.isPending}
              className="rounded-md border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
            >
              Revert to AI
            </button>
          ) : (
            <button
              onClick={() => setOverriding((v) => !v)}
              className="rounded-md border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
            >
              Override risk
            </button>
          )}
        </div>
      </div>

      <div className="px-5 py-4">
        {/* Provenance line. Which of these renders is the human-in-the-loop
            story made visible rather than buried in a column. */}
        <p className="text-xs text-slate-500">
          {isHuman ? (
            <>
              Source: <strong className="text-slate-700">Recruiter override</strong>
              {risk.overridden_at && (
                <> · {new Date(risk.overridden_at).toLocaleString("en-GB")}</>
              )}
            </>
          ) : (
            <>
              Source:{" "}
              <strong className="text-slate-700">
                {risk.source === "ai" ? "AI analysis" : "Rules engine"}
              </strong>
              {risk.last_analyzed_at && (
                <> · last analysed {new Date(risk.last_analyzed_at).toLocaleString("en-GB")}</>
              )}
              {candidate.analysis_provider === "mock" && (
                <span className="ml-2 rounded bg-amber-50 px-1.5 py-0.5 text-amber-800">
                  mock fixture
                </span>
              )}
            </>
          )}
        </p>

        {isHuman && risk.override_reason && (
          <p className="mt-2 rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-700">
            Reason: “{risk.override_reason}”
          </p>
        )}

        {/* The "Why?" list. This is the difference between an explainable
            assessment and a magic number. */}
        <h3 className="mt-4 text-xs font-semibold uppercase tracking-wide text-slate-500">
          Why?
        </h3>
        {risk.factors.length ? (
          <ul className="mt-2 space-y-1">
            {risk.factors.map((f) => (
              <li key={f} className="flex gap-2 text-sm text-slate-700">
                <span className="text-slate-400">•</span>
                {f}
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-2 text-sm text-slate-500">
            No risk factors detected.
          </p>
        )}

        {risk.signals.length > 0 && (
          <>
            <h3 className="mt-4 text-xs font-semibold uppercase tracking-wide text-slate-500">
              Detected in the candidate&apos;s own words
            </h3>
            <ul className="mt-2 space-y-2">
              {risk.signals.map((s) => (
                <li
                  key={s.type}
                  className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2"
                >
                  <div className="text-xs font-medium text-slate-700">
                    {s.type.replace(/_/g, " ")}
                  </div>
                  {/* The verbatim quote. A signal without checkable evidence is
                      an unfalsifiable assertion, so the guardrail drops any
                      whose quote is absent from the transcript. */}
                  <div className="mt-0.5 text-sm italic text-slate-600">
                    “{s.evidence}”
                  </div>
                </li>
              ))}
            </ul>
          </>
        )}

        {candidate.next_action !== "NO_ACTION" && (
          <p className="mt-4 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm">
            <span className="font-semibold text-slate-900">Recommended: </span>
            {candidate.next_action_label}
            {candidate.recommended_follow_up && (
              <span className="block text-slate-600">
                {candidate.recommended_follow_up}
              </span>
            )}
          </p>
        )}

        {overriding && (
          <div className="mt-4 rounded-md border border-slate-300 bg-slate-50 p-4">
            <h4 className="text-sm font-semibold text-slate-900">
              Override the assessment
            </h4>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              {(["LOW", "MEDIUM", "HIGH"] as RiskLevel[]).map((l) => (
                <button
                  key={l}
                  onClick={() => setLevel(l)}
                  className={`rounded-md border px-3 py-1.5 text-xs font-medium ${
                    level === l
                      ? "border-slate-900 bg-slate-900 text-white"
                      : "border-slate-300 bg-white text-slate-700"
                  }`}
                >
                  {l}
                </button>
              ))}
            </div>
            <input
              className="mt-3 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
              placeholder="Why are you changing this? (required)"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
            <p className="mt-1 text-xs text-slate-500">
              {/* Stating why the field is mandatory, rather than just enforcing it. */}
              A reason is required so this decision is still interpretable weeks
              from now.
            </p>
            <div className="mt-3 flex gap-2">
              <button
                onClick={() => override.mutate()}
                disabled={reason.trim().length < 3 || override.isPending}
                className="rounded-md bg-slate-900 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40"
              >
                {override.isPending ? "Saving…" : "Save override"}
              </button>
              <button
                onClick={() => setOverriding(false)}
                className="rounded-md border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
