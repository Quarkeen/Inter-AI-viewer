import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api, type ReportDecision, type ShortlistedCandidate } from "../lib/api";
import { formatSlot, roundedScore } from "../lib/format";
import { Alert, Button, Card, Field, PageTitle, Select, Spinner } from "../components/ui";

const SYMBOLS: Record<string, string> = {
  Terminated: "⛔",
  Done: "🟢",
  Ongoing: "🟡",
  Missed: "🔴",
  Scheduled: "🕒",
  "Not scheduled": "⚪",
};

function statusLabel(item: ShortlistedCandidate): string {
  if (item.interview_status === "Scheduled" && item.starts_at) return `Scheduled at ${formatSlot(item.starts_at)}`;
  if (item.interview_status === "Missed" && item.session_id) return "Missed · incomplete";
  if (item.interview_status === "Missed" && item.invitation_status !== "sent")
    return "Missed · invitation not confirmed sent";
  return item.interview_status;
}

function CandidateRow({ item }: { item: ShortlistedCandidate }) {
  const [score, setScore] = useState<number | null | undefined>(undefined);
  const [decision, setDecision] = useState<ReportDecision | null>(null);
  const [ready, setReady] = useState(!item.report_id);
  const canReport = Boolean(
    item.session_id && ["Done", "Missed", "Terminated"].includes(item.interview_status),
  );

  useEffect(() => {
    if (!item.report_id) return;
    let active = true;
    api
      .reportDetail(item.report_id)
      .then((result) => {
        if (!active) return;
        setScore(result.report.overall_score ?? null);
        setDecision(result.decision);
        setReady(true);
      })
      .catch(() => {
        if (active) setReady(true);
      });
    return () => {
      active = false;
    };
  }, [item.report_id]);

  return (
    <Card>
      <div className="flex flex-wrap items-center gap-4">
        <div className="min-w-0 flex-1">
          <p className="truncate font-semibold">
            {item.full_name} · #{item.application_id}
          </p>
          <p className="text-sm text-ink-500">{item.role}</p>
        </div>
        <div className="w-64">
          <p className="text-xs text-ink-500">INTERVIEW</p>
          <p className="text-sm">
            {SYMBOLS[item.interview_status]} {statusLabel(item)}
          </p>
        </div>
        <div className="w-28">
          <p className="text-xs text-ink-500">SCORE</p>
          <p className="text-sm font-medium">
            {!ready ? "…" : score != null ? `${roundedScore(score)} / 100` : "—"}
          </p>
        </div>
        <div className="w-44">
          <p className="text-xs text-ink-500">HR DECISION</p>
          <p className="text-sm">{decision ? decision.outcome : item.report_id ? "Pending" : "—"}</p>
        </div>
        <div className="w-32">
          {canReport && item.session_id ? (
            <Link
              to={`/candidate-report?session=${encodeURIComponent(item.session_id)}`}
              className="text-sm font-medium text-brand-700 hover:underline"
            >
              Open report ↗
            </Link>
          ) : (
            <span className="text-sm text-ink-500">—</span>
          )}
        </div>
      </div>
    </Card>
  );
}

export function Shortlisted() {
  const [candidates, setCandidates] = useState<ShortlistedCandidate[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [job, setJob] = useState<number | null>(null);
  const [status, setStatus] = useState("All");
  const [page, setPage] = useState(1);

  useEffect(() => {
    api
      .shortlisted()
      .then(setCandidates)
      .catch((err: unknown) =>
        setError(err instanceof ApiError ? err.message : "The candidate list could not be loaded. Please refresh and try again."),
      );
  }, []);

  useEffect(() => {
    setPage(1);
  }, [job, status]);

  if (error) return <Alert tone="error">{error}</Alert>;
  if (!candidates) return <Spinner label="Loading shortlisted candidates…" />;
  if (candidates.length === 0)
    return <Alert tone="info">Approved applicants will appear here after HR saves shortlist decisions.</Alert>;

  const roles = new Map<number, string>();
  for (const item of candidates) roles.set(item.job_id, item.role);
  const filtered = candidates.filter(
    (item) => (job == null || item.job_id === job) && (status === "All" || item.interview_status === status),
  );
  const pageCount = Math.max(1, Math.ceil(filtered.length / 8));
  const safePage = Math.min(page, pageCount);
  const visible = filtered.slice((safePage - 1) * 8, safePage * 8);

  return (
    <div>
      <PageTitle
        title="Shortlisted candidates"
        caption="Track approved applicants, open their interview reports, and review HR decisions."
      />
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        {[
          ["Shortlisted", candidates.length],
          ["Done", candidates.filter((item) => item.interview_status === "Done").length],
          ["Ongoing", candidates.filter((item) => item.interview_status === "Ongoing").length],
          ["Missed", candidates.filter((item) => item.interview_status === "Missed").length],
        ].map(([label, value]) => (
          <Card key={label as string} className="py-3">
            <p className="text-xs text-ink-500">{label}</p>
            <p className="text-2xl font-semibold">{value}</p>
          </Card>
        ))}
      </div>
      <div className="mb-4 grid gap-3 md:grid-cols-2">
        <Field label="Role">
          <Select value={job ?? ""} onChange={(event) => setJob(event.target.value ? Number(event.target.value) : null)}>
            <option value="">All roles</option>
            {[...roles].map(([id, role]) => (
              <option key={id} value={id}>
                #{id} · {role}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Interview status">
          <Select value={status} onChange={(event) => setStatus(event.target.value)}>
            {["All", "Done", "Ongoing", "Missed", "Terminated", "Scheduled", "Not scheduled"].map((option) => (
              <option key={option}>{option}</option>
            ))}
          </Select>
        </Field>
      </div>
      {filtered.length === 0 ? (
        <Alert tone="info">No applicants match this selection.</Alert>
      ) : (
        <div className="space-y-3">
          {visible.map((item) => (
            <CandidateRow key={`${item.application_id}-${item.interview_id ?? "none"}`} item={item} />
          ))}
          <div className="flex items-center justify-between">
            <p className="text-sm text-ink-500">
              Showing {(safePage - 1) * 8 + 1}–{Math.min(safePage * 8, filtered.length)} of {filtered.length}
            </p>
            {pageCount > 1 && (
              <div className="flex gap-2">
                <Button variant="secondary" disabled={safePage === 1} onClick={() => setPage(safePage - 1)}>
                  Previous
                </Button>
                <Button variant="secondary" disabled={safePage === pageCount} onClick={() => setPage(safePage + 1)}>
                  Next
                </Button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
