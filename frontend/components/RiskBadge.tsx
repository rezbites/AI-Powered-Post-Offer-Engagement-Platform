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

/**
 * A risk band never renders alone. `source` is shown alongside because a
 * recruiter must be able to tell at a glance whether they are looking at a
 * model output or a colleague's judgement.
 */
export function RiskBadge({
  level,
  source,
  confidence,
  size = "md",
}: {
  level: RiskLevel;
  source?: RiskSource;
  confidence?: number;
  size?: "sm" | "md";
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md border font-medium ${STYLES[level]} ${
        size === "sm" ? "px-2 py-0.5 text-xs" : "px-2.5 py-1 text-sm"
      }`}
    >
      <span className={`h-2 w-2 rounded-full ${DOT[level]}`} />
      {level}
      {confidence !== undefined && (
        <span className="font-normal opacity-70">
          {Math.round(confidence * 100)}%
        </span>
      )}
      {source === "human" && (
        <span className="font-normal opacity-70">· overridden</span>
      )}
    </span>
  );
}
