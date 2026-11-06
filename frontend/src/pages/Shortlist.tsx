import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api, type Application, type Job, type ShortlistItem, type ShortlistRun } from "../lib/api";
import { roundedScore } from "../lib/format";
import { ResumeEvidence } from "../components/ResumeEvidence";
import { Alert, Button, Card, Field, Input, Modal, PageTitle, Select, Spinner } from "../components/ui";

const LABELS: Record<string, string> = {
  required_skills: "Required skills",
  relevant_experience: "Relevant experience",
  projects: "Projects",
  preferred_qualifications: "Preferred qualifications",
};

export function Shortlist() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [jobId, setJobId] = useState<number | null>(null);
  const [weights, setWeights] = useState<Record<string, number>>({});
  const [threshold, setThreshold] = useState(70);
  const [run, setRun] = useState<ShortlistRun | null>(null);
  const [applications, setApplications] = useState<Application[]>([]);
  const [items, setItems] = useState<ShortlistItem[]>([]);
  const [approvals, setApprovals] = useState<Record<number, boolean>>({});
  const [evidence, setEvidence] = useState<ShortlistItem | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);

  useEffect(() => {
    Promise.all([api.jobs(), api.weights()])
      .then(([loadedJobs, loadedWeights]) => {
        setJobs(loadedJobs);
        setWeights(loadedWeights.weights);
        if (loadedJobs.length > 0) setJobId(loadedJobs[0].job_id);
      })
      .catch((err: unknown) =>
        setError(err instanceof ApiError ? err.message : "The shortlist could not be loaded or saved. Please try again."),
      )
      .finally(() => setLoading(false));
  }, []);

  async function refresh(selected: number) {
    const [result, received] = await Promise.all([api.latestShortlist(selected), api.applications(selected)]);
    setApplications(received);
    setRun(result.run);
    setItems(result.items);
    const next: Record<number, boolean> = {};
    for (const item of result.items) {
      next[item.application_id] =
        result.run?.status === "completed" ? item.approved === 1 : item.proposed === 1;
    }
    setApprovals(next);
  }

  useEffect(() => {
    if (jobId == null) return;
    let active = true;
    setError(null); setRun(null); setItems([]); setApplications([]);
    Promise.all([api.latestShortlist(jobId), api.applications(jobId)])
      .then(([result, received]) => {
        if (!active) return;
        setRun(result.run); setItems(result.items); setApplications(received);
        const next: Record<number, boolean> = {};
        for (const item of result.items) next[item.application_id] = result.run?.status === "completed" ? item.approved === 1 : item.proposed === 1;
        setApprovals(next);
      })
      .catch((err: unknown) => { if (active) setError(err instanceof ApiError ? err.message : "The shortlist could not be loaded. Please retry."); });
    const refreshApplicants = () => { void api.applications(jobId).then(received => { if (active) setApplications(received); }).catch(() => {}); };
    window.addEventListener("focus", refreshApplicants);
    const timer = window.setInterval(refreshApplicants, 15000);
    return () => { active = false; window.removeEventListener("focus", refreshApplicants); window.clearInterval(timer); };
  }, [jobId]);

  async function generate() {
    if (jobId == null) return;
    setError(null);
    setNotice(null);
    setWorking(true);
    try {
      const result = await api.generateShortlist(jobId, threshold);
      await refresh(jobId);
      setNotice(`Shortlist generated.`);
      void result;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The shortlist could not be loaded or saved. Please try again.");
    } finally {
      setWorking(false);
    }
  }

  async function continueRun() {
    if (!run) return;
    setError(null);
    setWorking(true);
    try {
      await api.continueShortlist(run.run_id);
      if (jobId != null) await refresh(jobId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The shortlist could not be loaded or saved. Please try again.");
    } finally {
      setWorking(false);
    }
  }

  async function save() {
    if (!run) return;
    setError(null);
    setWorking(true);
    try {
      await api.saveDecisions(
        run.run_id,
        items.map((item) => ({ application_id: item.application_id, approved: approvals[item.application_id] ?? false })),
      );
      if (jobId != null) await refresh(jobId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The shortlist could not be loaded or saved. Please try again.");
    } finally {
      setWorking(false);
    }
  }

  if (loading) return <Spinner label="Loading shortlist…" />;
  if (jobs.length === 0) return <Alert tone="info">Create a job and receive applications before generating a shortlist.</Alert>;

  const reviewed = new Set(items.map(item => item.application_id));
  const pending = applications.filter(item => !reviewed.has(item.application_id));
  const completed = run?.status === "completed";
  const approvedCount = items.filter((item) => item.approved === 1).length;

  return (
    <div>
      <PageTitle
        title="Shortlist review"
        caption="Generate recommendations, review their evidence, and decide who progresses to interview."
      />
      {error && (
        <div className="mb-4">
          <Alert tone="error">{error}</Alert>
        </div>
      )}
      {notice && (
        <div className="mb-4">
          <Alert tone="success">{notice}</Alert>
        </div>
      )}
      <Card>
        <Field label="Select a job">
          <Select disabled={working} value={jobId ?? ""} onChange={(event) => setJobId(Number(event.target.value))}>
            {jobs.map((job) => (
              <option key={job.job_id} value={job.job_id}>
                #{job.job_id} · {job.title}
              </option>
            ))}
          </Select>
        </Field>
        <div className="mt-4 rounded-lg border border-slate-200 p-4">
          <p className="font-semibold">Resume evaluation criteria</p>
          <div className="mt-2 grid grid-cols-2 gap-3 md:grid-cols-4">
            {Object.entries(weights).map(([name, weight]) => (
              <div key={name}>
                <p className="text-xs text-ink-500">{LABELS[name] ?? name}</p>
                <p className="font-semibold">{weight}%</p>
              </div>
            ))}
          </div>
          <p className="mt-2 text-xs text-ink-500">
            Candidates are proposed when their total weighted resume score is greater than the threshold. Preferred qualifications are excluded when the JD has none.
          </p>
        </div>
        <div className="mt-4 flex flex-wrap items-end gap-4">
          <Field label="Selection threshold">
            <Input
              type="number"
              min={0}
              max={100}
              step={1}
              value={threshold}
              onChange={(event) => setThreshold(Number(event.target.value))}
              className="w-32"
            />
          </Field>
          <p className="flex-1 text-xs text-ink-500">Generating a new shortlist replaces any pending review for this job.</p>
          <Button loading={working} onClick={() => void generate()}>
            Generate shortlist
          </Button>
        </div>
      </Card>

      {!run ? (
        <div className="mt-4">
          <Alert tone="info">Choose a threshold and generate the first shortlist for this job.</Alert>
        </div>
      ) : run.status === "evaluating" ? (
        <div className="mt-4 space-y-3">
          <Alert tone="info">
            This run has not finished evaluating. Continue to reuse completed results and finish the remaining applications.
          </Alert>
          <Button loading={working} onClick={() => void continueRun()}>
            Continue evaluation
          </Button>
        </div>
      ) : (
        <div className="mt-4 space-y-3">
          {completed ? (
            <Alert tone="success">HR decisions saved. {approvedCount} application(s) approved for interview.</Alert>
          ) : (
            <Alert tone="info">Paused for HR review. Check the applications you approve, then save your decisions.</Alert>
          )}
          {completed && (
            <Link to="/schedule" className="inline-block text-sm font-medium text-brand-700 hover:underline">
              Schedule approved applicants →
            </Link>
          )}
          <p className="text-sm text-ink-500">
            {items.filter((item) => item.proposed).length} proposed · {items.length} applications reviewed
          </p>
          {items.map((item) => (
            <Card key={item.application_id}>
              <div className="flex flex-wrap items-center gap-4">
                <div className="min-w-0 flex-1">
                  <p className="truncate font-semibold">{item.full_name}</p>
                  <p className="text-xs text-ink-500">
                    #{item.application_id} · {item.email}
                  </p>
                </div>
                <div className="w-28">
                  <p className="text-xs text-ink-500">RESUME MATCH</p>
                  <p className="text-2xl font-semibold">
                    {roundedScore(item.score)} <span className="text-sm font-normal text-ink-500">/ 100</span>
                  </p>
                </div>
                <div className="w-32">
                  <p className="text-xs text-ink-500">AI RECOMMENDATION</p>
                  <p className="text-sm">{item.proposed ? "Proposed" : "Not proposed"}</p>
                </div>
                <Button variant="secondary" onClick={() => setEvidence(item)}>
                  Evidence
                </Button>
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={approvals[item.application_id] ?? false}
                    disabled={completed}
                    onChange={(event) =>
                      setApprovals((prev) => ({ ...prev, [item.application_id]: event.target.checked }))
                    }
                  />
                  Approve for interview
                </label>
              </div>
            </Card>
          ))}
          <Button loading={working} disabled={completed} onClick={() => void save()}>
            Save HR decisions
          </Button>
        </div>
      )}
      {pending.length > 0 && <section className="mt-5 space-y-3" aria-label="Applications awaiting review">
        <div><h2 className="font-semibold">Awaiting review <span className="font-normal text-ink-500">({pending.length})</span></h2><p className="mt-1 text-xs text-ink-500">Generate a new shortlist to evaluate and include these applications. Existing HR decisions remain saved.</p></div>
        {pending.map(item => <Card key={item.application_id}><div className="flex flex-wrap items-center justify-between gap-3"><div><p className="font-semibold">{item.full_name}</p><p className="mt-1 text-xs text-ink-500">#{item.application_id} · {item.email}</p></div><span className="rounded-full bg-slate-100 px-3 py-1 text-xs text-ink-500">Not included in this review</span></div></Card>)}
      </section>}
      {evidence && (
        <Modal title={`Evidence · ${evidence.full_name}`} onClose={() => setEvidence(null)}>
          <ResumeEvidence assessment={evidence.assessment} />
        </Modal>
      )}
    </div>
  );
}
