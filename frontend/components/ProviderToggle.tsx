"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

/**
 * Choose which provider serves the next call.
 *
 * Exists so the difference is demonstrable rather than asserted: run the same
 * candidate through the mock and through Gemini and watch the signals, the
 * latency and the model-versus-engine agreement change.
 *
 * The Gemini option is disabled when no key is configured. Offering a choice
 * that would silently fall back to the mock — and then labelling the result as
 * a live analysis — is precisely the dishonesty the whole Demo Mode labelling
 * scheme exists to prevent.
 */
export function ProviderToggle({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const { data } = useQuery({ queryKey: ["ai-status"], queryFn: api.aiStatus });
  const geminiReady = data?.available?.gemini ?? false;

  const options = [
    { key: "mock", label: "Mock", enabled: true, hint: "Deterministic. Instant, free." },
    {
      key: "gemini",
      label: data?.model ?? "Gemini",
      enabled: geminiReady,
      hint: geminiReady
        ? "Real model call. ~5s, uses quota."
        : "No GEMINI_API_KEY configured.",
    },
  ];

  return (
    <div className="flex items-center gap-1.5">
      <span className="text-xs text-slate-400">run with</span>
      <div className="flex overflow-hidden rounded-md border border-slate-300">
        {options.map((o) => (
          <button
            key={o.key}
            title={o.hint}
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
