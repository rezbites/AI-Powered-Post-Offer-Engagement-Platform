import type { RiskLevel, RiskSource } from "@/types/api";

const STYLES: Record<RiskLevel, string> = {
  HIGH: "bg-red-50 text-red-700 border-red-200",
  MEDIUM: "bg-amber-50 text-amber-700 border-amber-200",
  LOW: "bg-emerald-50 text-emerald-700 border-emerald-200",
};

const DOT: Record<RiskLevel, string> = {
  HIGH: "bg-red-500",
  MEDIUM: "bg-amber-500",
  LOW: "bg-emerald-500",
};

/** Plain English, because "LOW" alone does not say low *what*. */
export const RISK_MEANING: Record<RiskLevel, string> = {
  HIGH: "Likely to drop out — act now",
  MEDIUM: "Something needs resolving",
  LOW: "On track to join",
};

/**
 * A risk band.
 *
 * The confidence figure is deliberately **not** rendered inside this badge.
 * It used to be, and `LOW 95%` reads as a single number — as though the
 * candidate were 95% low-risk. They are independent axes: the band is how
 * likely someone is to drop out, the confidence is how much evidence backs
 * that call. Gluing them together destroys the distinction the whole design
 * rests on.
 *
 * Confidence is shown separately and explicitly labelled by the caller.
 */
export function RiskBadge({
  level,
  source,
  size = "md",
  showMeaning = false,
}: {
  level: RiskLevel;
  source?: RiskSource;
  size?: "sm" | "md";
  showMeaning?: boolean;
}) {
  return (
    <span
      title={RISK_MEANING[level]}
      className={`inline-flex items-center gap-1.5 rounded-md border font-medium ${STYLES[level]} ${
        size === "sm" ? "px-2 py-0.5 text-xs" : "px-2.5 py-1 text-sm"
      }`}
    >
      <span className={`h-2 w-2 rounded-full ${DOT[level]}`} />
      {level} RISK
      {showMeaning && (
        <span className="font-normal opacity-80">· {RISK_MEANING[level]}</span>
      )}
      {source === "human" && (
        <span className="font-normal opacity-70">· overridden</span>
      )}
    </span>
  );
}

/**
 * Evidence strength, as a separate control from the band.
 *
 * Labelled "evidence" rather than "confidence" because confidence invites the
 * reading "confident the answer is LOW", which is the confusion being avoided.
 * The wording says what the number is actually derived from.
 */
export function ConfidenceMeter({
  value,
  detail,
}: {
  value: number;
  detail?: string;
}) {
  const pct = Math.round(value * 100);

  // Deliberately coarse. The underlying figure is an uncalibrated ordinal, and
  // three buckets are all it can honestly support.
  const band =
    pct >= 75 ? "Strong evidence" : pct >= 45 ? "Some evidence" : "Thin evidence";

  const tone =
    pct >= 75 ? "text-slate-700" : pct >= 45 ? "text-slate-600" : "text-amber-700";

  return (
    <span
      className="inline-flex items-center gap-2"
      title={
        detail ??
        "How much evidence supports this assessment — not the probability of the band being correct."
      }
    >
      <span className="flex h-1.5 w-16 overflow-hidden rounded-full bg-slate-200">
        <span
          className={`h-full rounded-full ${
            pct >= 75 ? "bg-slate-600" : pct >= 45 ? "bg-slate-400" : "bg-amber-400"
          }`}
          style={{ width: `${pct}%` }}
        />
      </span>
      <span className={`text-xs ${tone}`}>
        {band} · {pct}%
      </span>
    </span>
  );
}
