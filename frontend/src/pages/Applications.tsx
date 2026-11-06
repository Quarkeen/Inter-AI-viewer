import { useEffect, useMemo, useState } from "react";
import { ApiError, api, saveDownload, type Application, type Job, type ResumeEvaluation } from "../lib/api";
import { formatDate, initials, roundedScore } from "../lib/format";
import { ResumeEvidence } from "../components/ResumeEvidence";
import { Alert, Badge, Button, Card, Field, Input, Modal, PageTitle, Select, Spinner } from "../components/ui";

function ApplicationRow({ application }: { application: Application }) {
  const [evaluation, setEvaluation] = useState<ResumeEvaluation | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [evaluating, setEvaluating] = useState(false);
  const [showEvidence, setShowEvidence] = useState(false);

  useEffect(() => {
    let active = true;
    api
      .applicationDetail(application.application_id)
      .then((result) => {
        if (active) setEvaluation(result.evaluation);
      })
      .catch((err: unknown) => {
        if (active) setError(err instanceof ApiError ? err.message : "The evaluation could not be loaded.");
      });
    return () => {
      active = false;
    };
  }, [application.application_id]);

  async function evaluate() {
    setError(null);
    setEvaluating(true);
    try {
      const result = await api.evaluateApplication(application.application_id);
      setEvaluation(result.evaluation);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The evaluation could not be loaded or saved. Please try again.");
    } finally {
      setEvaluating(false);
    }
  }

  async function download() {
    setError(null);
    try {
      const file = await api.resumeFile(application.application_id);
      saveDownload(file.blob, file.filename);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The resume could not be downloaded.");
    }
  }

  const score = roundedScore(evaluation?.score);
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-4">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <div className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-brand-600 text-sm font-semibold text-white" aria-hidden>
            {initials(application.full_name)}
          </div>
          <div className="min-w-0">
            <p className="truncate font-semibold">{application.full_name}</p>
            <a className="block truncate text-sm text-ink-500 hover:underline" href={`mailto:${application.email}`}>
              {application.email}
            </a>
            <p className="text-xs text-ink-500">
              #{application.application_id} · Applied {formatDate(application.applied_at)}
            </p>
          </div>
        </div>
        <div className="min-w-40">
          <p className="text-xs text-ink-500">APPLIED FOR</p>
          <p className="text-sm font-medium">{application.job_title_at_application}</p>
          {application.current_job_title !== application.job_title_at_application && (
            <p className="text-xs text-ink-500">Current title: {application.current_job_title}</p>
          )}
        </div>
        <div className="w-28">
          <p className="text-xs text-ink-500">RESUME MATCH</p>
          {evaluation === undefined ? (
            <Spinner />
          ) : score != null ? (
            <p className="text-2xl font-semibold">
              {score} <span className="text-sm font-normal text-ink-500">/ 100</span>
            </p>
          ) : (
            <Button variant="secondary" loading={evaluating} onClick={() => void evaluate()}>
              Evaluate
            </Button>
          )}
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" disabled={!evaluation} onClick={() => setShowEvidence(true)}>
            Evidence
          </Button>
          <Button variant="secondary" onClick={() => void download()}>
            Resume
          </Button>
        </div>
      </div>
      {error && (
        <div className="mt-3">
          <Alert tone="error">{error}</Alert>
        </div>
      )}
      {showEvidence && evaluation && (
        <Modal title={`Evidence · ${application.full_name}`} onClose={() => setShowEvidence(false)}>
          <ResumeEvidence assessment={evaluation.assessment} />
        </Modal>
      )}
    </Card>
  );
}

export function Applications() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [applications, setApplications] = useState<Application[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<number | null>(null);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);

  useEffect(() => {
    api.jobs().then(setJobs).catch(() => setJobs([]));
  }, []);

  useEffect(() => {
    setApplications(null);
    setError(null);
    api
      .applications(jobId)
      .then(setApplications)
      .catch((err: unknown) =>
        setError(err instanceof ApiError ? err.message : "Applications could not be loaded. Please refresh and try again."),
      );
  }, [jobId]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (applications ?? []).filter(
      (item) =>
        !needle || item.full_name.toLowerCase().includes(needle) || item.email.toLowerCase().includes(needle),
    );
  }, [applications, query]);

  useEffect(() => {
    setPage(0);
  }, [jobId, query]);

  if (error) return <Alert tone="error">{error}</Alert>;
  if (!applications) return <Spinner label="Loading applications…" />;

  const pageSize = 6;
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const safePage = Math.min(page, pages - 1);
  const start = safePage * pageSize;
  const visible = filtered.slice(start, start + pageSize);

  return (
    <div>
      <PageTitle title="Candidate applications" caption="Review applicants, compare resume matches and open the evidence when needed." />
      <div className="mb-4 grid gap-3 md:grid-cols-[1fr_1.4fr]">
        <Field label="Filter by job">
          <Select value={jobId ?? ""} onChange={(event) => setJobId(event.target.value ? Number(event.target.value) : null)}>
            <option value="">All jobs</option>
            {jobs.map((job) => (
              <option key={job.job_id} value={job.job_id}>
                #{job.job_id} · {job.title}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Search candidates">
          <Input value={query} placeholder="Search by name or email" onChange={(event) => setQuery(event.target.value)} />
        </Field>
      </div>
      {filtered.length === 0 ? (
        <Alert tone="info">No applications match this selection.</Alert>
      ) : (
        <>
          <div className="mb-3 flex items-center justify-between gap-3">
            <p className="text-sm text-ink-500">
              {filtered.length} {filtered.length === 1 ? "application" : "applications"} · Showing {start + 1}–
              {Math.min(start + pageSize, filtered.length)} · Page {safePage + 1} of {pages}
            </p>
            <div className="flex gap-2">
              <Button variant="secondary" disabled={safePage === 0} onClick={() => setPage(safePage - 1)}>
                Previous
              </Button>
              <Button variant="secondary" disabled={safePage === pages - 1} onClick={() => setPage(safePage + 1)}>
                Next
              </Button>
            </div>
          </div>
          <div className="space-y-3">
            {visible.map((application) => (
              <ApplicationRow key={application.application_id} application={application} />
            ))}
          </div>
          <div className="mt-3">
            <Badge tone="neutral">
              {filtered.length} reviewed · {filtered.length - visible.length} on other pages
            </Badge>
          </div>
        </>
      )}
    </div>
  );
}
