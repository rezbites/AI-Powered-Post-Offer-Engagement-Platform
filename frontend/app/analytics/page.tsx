"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export default function AnalyticsPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["analytics"],
    queryFn: api.analytics,
  });

  if (isLoading) return <p className="text-sm text-slate-500">Loading metrics…</p>;
  if (error || !data)
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800">
        Could not load analytics.
      </div>
    );

  const { totals, conversion, joining_windows, risk, engagement, ai_operations } = data;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">Analytics</h1>
        <p className="mt-1 text-sm text-slate-600">As of {data.generated_for}</p>
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Tile label="Total offered" value={totals.total_offered} />
        <Tile
          label="Offer-to-join conversion"
          // null is not 0: it means nothing has resolved yet. Rendering them
          // identically would report "0% conversion" for a healthy pipeline.
          value={
            conversion.resolved_rate === null
              ? "no data yet"
              : `${conversion.resolved_rate}%`
          }
          hint={`${conversion.joined} of ${conversion.resolved} resolved · ${conversion.pending_outcome} pending`}
        />
        <Tile
          label="High risk"
          value={risk.high}
          hint={`${risk.high_risk_joining_within_7_days} joining within 7 days`}
          tone={risk.high > 0 ? "warn" : undefined}
        />
        <Tile
          label="Joining soon"
          value={joining_windows.next_7_days}
          hint={`${joining_windows.next_15_days} in 15d · ${joining_windows.next_30_days} in 30d`}
        />
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Tile label="Active candidates" value={totals.active} />
        <Tile
          label="Engagement frequency"
          value={`${engagement.avg_interactions_per_week}/wk`}
          hint={`${engagement.avg_interactions_per_candidate} per candidate overall`}
        />
        <Tile
          label="Gone quiet"
          value={engagement.candidates_silent_over_7_days}
          hint={`${engagement.candidates_never_contacted} never contacted`}
          tone={engagement.candidates_silent_over_7_days > 0 ? "warn" : undefined}
        />
        <Tile
          label="Risk overridden by humans"
          value={risk.human_overridden}
          hint={`${risk.ai_assessed} assessed by AI`}
        />
      </div>

      {/* Stage drop-off. This is the metric that justifies storing journey
          progress as per-stage rows rather than a single current_stage column. */}
      <section className="rounded-lg border border-slate-200 bg-white">
        <h2 className="border-b border-slate-200 px-5 py-3 text-sm font-semibold text-slate-900">
          Engagement funnel
        </h2>
        <div className="space-y-3 px-5 py-4">
          {data.stages.map((s) => (
            <div key={s.key}>
              <div className="flex items-baseline justify-between text-sm">
                <span className="text-slate-700">{s.label}</span>
                <span className="text-slate-500">
                  {s.completed} completed · {s.completion_rate}%
                  {s.drop_off_from_previous > 0 && (
                    <span className="ml-2 text-amber-700">
                      −{s.drop_off_from_previous} dropped off
                    </span>
                  )}
                </span>
              </div>
              <div className="mt-1 h-2 overflow-hidden rounded-full bg-slate-100">
                <div
                  className="h-full rounded-full bg-slate-600"
                  style={{ width: `${s.completion_rate}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white">
        <div className="border-b border-slate-200 px-5 py-3">
          <h2 className="text-sm font-semibold text-slate-900">
            Recruiter performance
          </h2>
          <p className="mt-0.5 text-xs text-slate-500">
            {/* Honesty about the statistics. With a handful of resolved
                candidates each, one dropout moves a rate by tens of points. */}
            Small samples — a conversation starter, not a performance metric.
          </p>
        </div>
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-5 py-2 font-medium">Recruiter</th>
              <th className="px-3 py-2 font-medium">Candidates</th>
              <th className="px-3 py-2 font-medium">Conversion</th>
              <th className="px-3 py-2 font-medium">High risk</th>
              <th className="px-3 py-2 font-medium">Avg days since contact</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {data.recruiters.map((r) => (
              <tr key={r.recruiter_id}>
                <td className="px-5 py-2.5 font-medium text-slate-900">
                  {r.recruiter_name}
                </td>
                <td className="px-3 py-2.5 text-slate-700">{r.total_candidates}</td>
                <td className="px-3 py-2.5 text-slate-700">
                  {r.conversion_rate === null ? (
                    <span className="text-slate-400">no data yet</span>
                  ) : (
                    <>
                      {r.conversion_rate}%
                      <span className="ml-1 text-xs text-slate-400">
                        ({r.joined}/{r.resolved})
                      </span>
                    </>
                  )}
                </td>
                <td className="px-3 py-2.5 text-slate-700">{r.high_risk_active}</td>
                <td className="px-3 py-2.5 text-slate-700">
                  {r.avg_days_since_interaction ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {/* LLM observability, read straight from the analyses ledger. */}
      <section className="rounded-lg border border-slate-200 bg-white">
        <h2 className="border-b border-slate-200 px-5 py-3 text-sm font-semibold text-slate-900">
          AI operations
          <span className="ml-2 font-normal text-slate-500">
            {ai_operations.provider} · {ai_operations.mode} mode
          </span>
        </h2>
        <div className="grid grid-cols-2 gap-4 px-5 py-4 lg:grid-cols-4">
          <Small label="Analyses" value={ai_operations.total_analyses} />
          <Small label="Valid" value={ai_operations.valid} />
          <Small
            label="Repaired"
            value={ai_operations.repaired}
            hint="recovered after failing validation"
          />
          <Small
            label="Failed"
            value={ai_operations.failed}
            hint="fell back to deterministic assessment"
          />
          <Small
            label="Signals dropped"
            value={ai_operations.dropped_signals}
            hint="quote not found in candidate messages"
          />
          <Small
            label="Model/engine disagreements"
            value={ai_operations.model_engine_disagreements}
            hint="the engine is authoritative"
          />
          <Small
            label="Avg latency"
            value={
              ai_operations.avg_latency_ms === null
                ? "—"
                : `${ai_operations.avg_latency_ms}ms`
            }
          />
          <Small
            label="Tokens"
            value={`${ai_operations.total_tokens_in}/${ai_operations.total_tokens_out}`}
            hint="in / out"
          />
        </div>
      </section>
    </div>
  );
}

function Tile({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string | number;
  hint?: string;
  tone?: "warn";
}) {
  return (
    <div
      className={`rounded-lg border bg-white px-4 py-3 ${
        tone === "warn" ? "border-amber-200" : "border-slate-200"
      }`}
    >
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div
        className={`mt-1 text-2xl font-semibold ${
          tone === "warn" ? "text-amber-700" : "text-slate-900"
        }`}
      >
        {value}
      </div>
      {hint && <div className="mt-0.5 text-xs text-slate-500">{hint}</div>}
    </div>
  );
}

function Small({
  label,
  value,
  hint,
}: {
  label: string;
  value: string | number;
  hint?: string;
}) {
  return (
    <div>
      <div className="text-xs text-slate-500">{label}</div>
      <div className="text-lg font-semibold text-slate-900">{value}</div>
      {hint && <div className="text-xs text-slate-400">{hint}</div>}
    </div>
  );
}
