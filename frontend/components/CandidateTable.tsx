"use client";

import Link from "next/link";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type CandidateFilters } from "@/lib/api";
import { RiskBadge } from "./RiskBadge";
import type { RiskLevel } from "@/types/api";

const STATUSES = [
  { value: "", label: "Any status" },
  { value: "offer_accepted", label: "Offer accepted" },
  { value: "engaged", label: "Engaged" },
  { value: "at_risk", label: "At risk" },
  { value: "joined", label: "Joined" },
  { value: "dropped_out", label: "Dropped out" },
];

/** Next twelve months, for the joining-month filter the brief requires. */
function monthOptions(): { value: string; label: string }[] {
  const now = new Date();
  const out = [{ value: "", label: "Any month" }];
  for (let i = -2; i < 10; i++) {
    const d = new Date(now.getFullYear(), now.getMonth() + i, 1);
    const value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
    out.push({
      value,
      label: d.toLocaleString("en-GB", { month: "short", year: "numeric" }),
    });
  }
  return out;
}

export function CandidateTable() {
  const [filters, setFilters] = useState<CandidateFilters>({ limit: 25, offset: 0 });

  const { data, isLoading } = useQuery({
    queryKey: ["candidates", filters],
    queryFn: () => api.candidates(filters),
  });

  const { data: roles } = useQuery({ queryKey: ["roles"], queryFn: api.roles });
  const { data: recruiters } = useQuery({
    queryKey: ["recruiters"],
    queryFn: api.recruiters,
  });

  // Any filter change resets to the first page: leaving the offset in place
  // silently shows an empty table when the new result set is shorter.
  const update = (patch: Partial<CandidateFilters>) =>
    setFilters((f) => ({ ...f, ...patch, offset: 0 }));

  const select =
    "rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-700";

  return (
    <section className="rounded-lg border border-slate-200 bg-white">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 px-5 py-3">
        <input
          className={`${select} w-56`}
          placeholder="Search name or email"
          value={filters.search ?? ""}
          onChange={(e) => update({ search: e.target.value })}
        />
        <select
          className={select}
          value={filters.joining_month ?? ""}
          onChange={(e) => update({ joining_month: e.target.value })}
        >
          {monthOptions().map((m) => (
            <option key={m.value} value={m.value}>
              {m.label}
            </option>
          ))}
        </select>
        <select
          className={select}
          value={filters.role_title ?? ""}
          onChange={(e) => update({ role_title: e.target.value })}
        >
          <option value="">Any role</option>
          {(roles ?? []).map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
        <select
          className={select}
          value={filters.recruiter_id ?? ""}
          onChange={(e) => update({ recruiter_id: e.target.value })}
        >
          <option value="">Any recruiter</option>
          {(recruiters ?? []).map((r) => (
            <option key={r.id} value={r.id}>
              {r.name}
            </option>
          ))}
        </select>
        <select
          className={select}
          value={filters.risk_level ?? ""}
          onChange={(e) =>
            update({ risk_level: (e.target.value || undefined) as RiskLevel })
          }
        >
          <option value="">Any risk</option>
          <option value="HIGH">High</option>
          <option value="MEDIUM">Medium</option>
          <option value="LOW">Low</option>
        </select>
        <select
          className={select}
          value={filters.status ?? ""}
          onChange={(e) => update({ status: e.target.value })}
        >
          {STATUSES.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>

        <span className="ml-auto text-sm text-slate-500">
          {isLoading ? "Loading…" : `${data?.total ?? 0} candidates`}
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-5 py-2.5 font-medium">Candidate</th>
              <th className="px-3 py-2.5 font-medium">Joining</th>
              <th className="px-3 py-2.5 font-medium">Last contact</th>
              <th className="px-3 py-2.5 font-medium">Risk</th>
              {/* The brief requires risk, why, and next action visible on the
                  list itself - not one click away. */}
              <th className="px-3 py-2.5 font-medium">Why</th>
              <th className="px-3 py-2.5 font-medium">Recommended action</th>
              <th className="px-3 py-2.5 font-medium">Journey</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {(data?.items ?? []).map((c) => (
              <tr key={c.id} className="hover:bg-slate-50">
                <td className="px-5 py-3">
                  <Link
                    href={`/candidates/${c.id}`}
                    className="font-medium text-slate-900 hover:underline"
                  >
                    {c.name}
                  </Link>
                  <div className="text-xs text-slate-500">
                    {c.role_title} · {c.location}
                  </div>
                </td>
                <td className="whitespace-nowrap px-3 py-3">
                  <div className="text-slate-900">
                    {c.days_to_joining < 0
                      ? `${Math.abs(c.days_to_joining)}d ago`
                      : `${c.days_to_joining} days`}
                  </div>
                  <div className="text-xs text-slate-500">{c.joining_date}</div>
                </td>
                <td className="whitespace-nowrap px-3 py-3">
                  {/* Silence is what the automation rules key on, so it
                      belongs on the row rather than one click away. */}
                  {c.days_since_interaction === null ? (
                    <span className="text-amber-700">never</span>
                  ) : (
                    <div
                      className={
                        c.days_since_interaction >= 5
                          ? "text-amber-700"
                          : "text-slate-700"
                      }
                    >
                      {c.days_since_interaction === 0
                        ? "today"
                        : `${c.days_since_interaction}d ago`}
                    </div>
                  )}
                </td>
                <td className="px-3 py-3">
                  <RiskBadge
                    level={c.risk.level}
                    source={c.risk.source}
                    confidence={c.risk.confidence}
                    size="sm"
                  />
                </td>
                <td className="max-w-xs px-3 py-3 text-xs text-slate-600">
                  {c.why.length ? c.why.join(" · ") : "—"}
                </td>
                <td className="px-3 py-3 text-slate-700">
                  {c.next_action === "NO_ACTION" ? (
                    <span className="text-slate-400">—</span>
                  ) : (
                    c.next_action_label
                  )}
                </td>
                <td className="px-3 py-3">
                  <div className="flex items-center gap-2">
                    <div className="h-1.5 w-16 overflow-hidden rounded-full bg-slate-200">
                      <div
                        className="h-full bg-slate-500"
                        style={{
                          width: `${
                            c.journey.total
                              ? (c.journey.completed / c.journey.total) * 100
                              : 0
                          }%`,
                        }}
                      />
                    </div>
                    <span className="text-xs text-slate-500">
                      {c.journey.completed}/{c.journey.total}
                    </span>
                  </div>
                  {c.journey.overdue_stages > 0 && (
                    <div className="mt-0.5 text-xs text-amber-700">
                      {c.journey.overdue_stages} overdue
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {!isLoading && data?.items.length === 0 && (
          <p className="px-5 py-8 text-center text-sm text-slate-500">
            No candidates match these filters.
          </p>
        )}
      </div>

      {data && data.total > (filters.limit ?? 25) && (
        <div className="flex items-center justify-between border-t border-slate-200 px-5 py-3 text-sm">
          <button
            className="rounded-md border border-slate-300 px-3 py-1.5 text-slate-700 disabled:opacity-40"
            disabled={(filters.offset ?? 0) === 0}
            onClick={() =>
              setFilters((f) => ({
                ...f,
                offset: Math.max(0, (f.offset ?? 0) - (f.limit ?? 25)),
              }))
            }
          >
            Previous
          </button>
          <span className="text-slate-500">
            {(filters.offset ?? 0) + 1}–
            {Math.min((filters.offset ?? 0) + (filters.limit ?? 25), data.total)} of{" "}
            {data.total}
          </span>
          <button
            className="rounded-md border border-slate-300 px-3 py-1.5 text-slate-700 disabled:opacity-40"
            disabled={(filters.offset ?? 0) + (filters.limit ?? 25) >= data.total}
            onClick={() =>
              setFilters((f) => ({
                ...f,
                offset: (f.offset ?? 0) + (f.limit ?? 25),
              }))
            }
          >
            Next
          </button>
        </div>
      )}
    </section>
  );
}
