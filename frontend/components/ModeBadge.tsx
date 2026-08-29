"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

/**
 * Persistent Demo/Live indicator.
 *
 * This exists so nobody can mistake deterministic mock output for real model
 * output. A reviewer seeing suspiciously consistent analyses is entitled to
 * wonder whether the "AI" is hardcoded; saying so plainly turns that suspicion
 * into evidence of deliberate design.
 */
export function ModeBadge() {
  const { data } = useQuery({ queryKey: ["ai-status"], queryFn: api.aiStatus });

  if (!data) return null;

  const demo = data.mode === "demo";

  return (
    <span
      title={data.description}
      className={`rounded-full border px-3 py-1 text-xs font-medium ${
        demo
          ? "border-amber-300 bg-amber-50 text-amber-800"
          : "border-emerald-300 bg-emerald-50 text-emerald-800"
      }`}
    >
      {demo ? "DEMO MODE - deterministic mock, no LLM calls" : `LIVE - ${data.model}`}
    </span>
  );
}
