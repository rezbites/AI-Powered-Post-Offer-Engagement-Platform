"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

/**
 * Choose which provider serves the next call.
 *
 * Exists so the difference is demonstrable rather than asserted: run the same
 * candidate through the mock and through a real model and watch the signals,
 * the latency and the model-versus-engine agreement change.
 *
 * The options are built from `/ai/status.available` rather than hardcoded.
 * They were hardcoded once, and when a third provider was added the list
 * silently went stale - the button rendered the *active* model's name while
 * still carrying the old provider's key, so it looked available, read as
 * "Claude", and was disabled. Deriving the list from the server is what stops
 * that recurring.
 *
 * A provider with no configured key is shown disabled rather than hidden, so
 * it is obvious that the option exists and why it cannot be used. Offering one
 * that would silently fall back to the mock - and then labelling the result as
 * a live analysis - is the dishonesty the whole Demo Mode scheme avoids.
 */

const LABELS: Record<string, string> = {
  mock: "Mock",
  gemini: "Gemini",
  claude: "Claude",
};

// Order matters: cheapest and fastest first, so the default reading order
// matches the order a recruiter would reach for them.
const ORDER = ["mock", "claude", "gemini"];

export function ProviderToggle({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const { data } = useQuery({ queryKey: ["ai-status"], queryFn: api.aiStatus });

  if (!data) return null;

  const available = data.available ?? { mock: true };
  const models = data.models ?? {};

  const options = ORDER.filter((key) => key in available).map((key) => ({
    key,
    label: LABELS[key] ?? key,
    model: models[key],
    enabled: Boolean(available[key]),
  }));

  return (
    <div className="flex items-center gap-1.5">
      <span className="text-xs text-slate-400">run with</span>
      <div className="flex overflow-hidden rounded-md border border-slate-300">
        {options.map((o) => (
          <button
            key={o.key}
            title={
              o.enabled
                ? o.key === "mock"
                  ? "Deterministic. Instant and free."
                  : `Real model call via ${o.model}. Slower, uses quota.`
                : `No API key configured for ${o.label}.`
            }
            disabled={!o.enabled}
            onClick={() => onChange(o.key)}
            className={`px-2.5 py-1 text-xs font-medium disabled:cursor-not-allowed disabled:opacity-40 ${
              value === o.key
                ? "bg-slate-900 text-white"
                : "bg-white text-slate-600 hover:bg-slate-50"
            }`}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}

/**
 * The provider a panel should start on.
 *
 * Defaults to whatever the server actually resolved, so the UI never opens
 * pre-selected on a provider that has no key - which is exactly how the
 * previous hardcoded default became unusable.
 */
export function useDefaultProvider(): string {
  const { data } = useQuery({ queryKey: ["ai-status"], queryFn: api.aiStatus });
  return data?.provider ?? "mock";
}
