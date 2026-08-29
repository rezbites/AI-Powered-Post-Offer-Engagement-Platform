import { AttentionQueue } from "@/components/AttentionQueue";
import { CandidateTable } from "@/components/CandidateTable";

/**
 * The recruiter's morning view.
 *
 * Ordering is the whole design: the ranked attention queue comes first and
 * answers "who needs me today?", with the full filterable table beneath for
 * everything else. A table-first dashboard would show the same data and answer
 * a less useful question.
 */
export default function DashboardPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">
          Who needs attention today?
        </h1>
        <p className="mt-1 text-sm text-slate-600">
          Candidates between offer acceptance and joining, ranked by urgency.
        </p>
      </div>

      <AttentionQueue />
      <CandidateTable />
    </div>
  );
}
