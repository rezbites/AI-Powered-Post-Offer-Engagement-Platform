"use client";

import Link from "next/link";
import { use } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Conversation } from "@/components/Conversation";
import { JourneyTimeline } from "@/components/JourneyTimeline";
import { MessageComposer } from "@/components/MessageComposer";
import { NotesPanel } from "@/components/NotesPanel";
import { RiskPanel } from "@/components/RiskPanel";

export default function CandidatePage({
  params,
}: {
  // Next 15 delivers route params as a promise; `use` unwraps it.
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);

  const { data, isLoading, error } = useQuery({
    queryKey: ["candidate", id],
    queryFn: () => api.candidate(id),
  });

  if (isLoading) {
    return <p className="text-sm text-slate-500">Loading candidate…</p>;
  }

  if (error || !data) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800">
        Could not load this candidate.{" "}
        <Link href="/" className="underline">
          Back to dashboard
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <Link href="/" className="text-sm text-slate-500 hover:underline">
          ← Dashboard
        </Link>
        <h1 className="mt-2 text-xl font-semibold text-slate-900">{data.name}</h1>
        <p className="text-sm text-slate-600">
          {data.role_title} · {data.location} · {data.recruiter_name}
        </p>
      </div>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Fact label="Offer date" value={data.offer_date} />
        <Fact label="Joining date" value={data.joining_date} />
        <Fact
          label="Days to joining"
          value={
            data.days_to_joining < 0
              ? `${Math.abs(data.days_to_joining)} days ago`
              : `${data.days_to_joining} days`
          }
        />
        <Fact
          label="Last contact"
          value={
            data.days_since_interaction === null
              ? "never"
              : `${data.days_since_interaction} days ago`
          }
        />
      </div>

      <RiskPanel candidate={data} />

      <NotesPanel candidate={data} />

      <div className="grid gap-6 lg:grid-cols-2">
        <JourneyTimeline candidate={data} />
        <Conversation candidate={data} />
      </div>

      <MessageComposer candidateId={data.id} />
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-4 py-3">
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 text-sm font-medium text-slate-900">{value}</div>
    </div>
  );
}
