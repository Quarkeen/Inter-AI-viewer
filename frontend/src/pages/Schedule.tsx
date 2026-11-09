import { useEffect, useRef, useState } from "react";
import { ApiError, api, type ApprovedCandidate, type Interview, type InterviewTemplate, type Job } from "../lib/api";
import { combineLocal, formatSlot } from "../lib/format";
import { MODE_LABELS } from "../lib/interviewTemplates";
import { Alert, Badge, Button, Card, Field, Input, Modal, PageTitle, Select, Spinner } from "../components/ui";
import { AutomaticSchedulePanel } from "../components/AutomaticSchedulePanel";

const LABELS: Record<string, string> = {
  scheduled: "Scheduled · invitation not sent",
  sending: "Sending attempt in progress · do not send again",
  sent: "Invitation sent",
  delivery_unknown: "Sending could not be confirmed · check the sender's Sent folder",
};

function tomorrow(): string {
  const date = new Date();
  date.setDate(date.getDate() + 1);
  return date.toISOString().slice(0, 10);
}

interface RowState {
  reason: string | null;
  expired: boolean;
  opens: string;
  closes: string;
}

function InterviewRow({
  interview,
  approvedIds,
  onChanged,
}: {
  interview: Interview;
  approvedIds: Set<number>;
  onChanged: () => void;
}) {
  const [state, setState] = useState<RowState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [rescheduleOpen, setRescheduleOpen] = useState(false);
  const [date, setDate] = useState(tomorrow());
  const [time, setTime] = useState("10:00");
  const [duration, setDuration] = useState(30);

  useEffect(() => {
    let active = true;
    Promise.all([api.interviewApproval(interview.interview_id), api.accessWindow(interview.starts_at, interview.ends_at)])
      .then(([approval, window]) => {
        if (!active) return;
        let reason = approval.ok ? null : approval.reason;
        if (reason && approvedIds.has(interview.application_id)) {
          reason = "This slot belongs to an earlier shortlist. Reschedule it to use the current approval.";
        }
        setState({ reason, expired: window.expired, opens: window.opens_at, closes: window.closes_at });
      })
      .catch(() => {
        if (active) setState({ reason: "Approval could not be checked.", expired: false, opens: "", closes: "" });
      });
    return () => {
      active = false;
    };
  }, [interview.interview_id, interview.starts_at, interview.ends_at, interview.application_id, approvedIds]);

  async function send() {
    setError(null);
    setSending(true);
    try {
      await api.sendInvitation(interview.interview_id);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The invitation could not be sent.");
    } finally {
      setSending(false);
    }
  }

  async function reschedule(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await api.rescheduleInterview(interview.interview_id, combineLocal(date, time), duration);
      setRescheduleOpen(false);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The schedule could not be saved.");
    }
  }

  const template = interview.template_json ? JSON.parse(interview.template_json) as InterviewTemplate : null;
  const minutes = Math.floor((new Date(interview.ends_at).getTime() - new Date(interview.starts_at).getTime()) / 60000);
  let label = LABELS[interview.status] ?? interview.status;
  if (interview.status === "scheduled" && interview.is_rescheduled) label = "Rescheduled · invitation not sent";
  const blocked = interview.status !== "scheduled" || (state?.expired ?? false) || state?.reason != null;

  return (
    <Card>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,14rem),1fr))] items-start gap-x-6 gap-y-5">
        <div className="min-w-0 space-y-1 [overflow-wrap:anywhere]">
          <p className="font-semibold">{interview.full_name}</p>
          <p className="text-sm text-ink-500">{interview.email}</p>
          <p className="text-xs leading-relaxed text-ink-500">
            #{interview.application_id} · {interview.job_title}
          </p>
        </div>
        <div className="min-w-0 space-y-1 [overflow-wrap:anywhere]">
          <p className="text-xs text-ink-500">INTERVIEW SLOT</p>
          <p className="text-sm leading-relaxed">
            <b>From</b> {formatSlot(interview.starts_at)}
            <br />
            <b>Until</b> {formatSlot(interview.ends_at)}
          </p>
          <p className="text-xs text-ink-500">Duration: {minutes} minutes</p>
        </div>
        <div className="min-w-0 space-y-1 [overflow-wrap:anywhere]">
          <p className="text-xs text-ink-500">INVITATION STATUS</p>
          <p className="text-sm leading-relaxed">{label}</p>
        </div>
      </div>
      <div className="mt-4 flex flex-wrap items-center justify-between gap-x-6 gap-y-3 border-t border-slate-100 pt-4">
        <div className="min-w-0 flex-1 basis-64 space-y-1 text-xs leading-relaxed text-ink-500 [overflow-wrap:anywhere]">
          <p>{template ? `${template.name} · v${template.version} · ${MODE_LABELS[template.mode ?? 'ai']} · ${template.main_questions} main / ${template.max_followups} follow-ups` : "Standard · 6 main / 4 follow-ups"}</p>
          <p>Auto-end on interruption: {interview.terminate_on_interruption ? `On · ${interview.warning_limit ?? 3} warnings allowed` : "Off"}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {approvedIds.has(interview.application_id) && interview.status !== "sending" && (
            <Button variant="secondary" className="whitespace-nowrap" onClick={() => setRescheduleOpen(true)}>
              Reschedule
            </Button>
          )}
          <Button className="whitespace-nowrap" loading={sending} disabled={blocked || state == null} onClick={() => void send()}>
            Send invitation
          </Button>
        </div>
      </div>
      {state?.reason && (
        <div className="mt-3">
          <Alert tone="warning">{state.reason}</Alert>
        </div>
      )}
      {state?.expired && interview.status === "scheduled" && (
        <div className="mt-3">
          <Alert tone="warning">This slot has expired. Save a future schedule before sending.</Alert>
        </div>
      )}
      {error && (
        <div className="mt-3">
          <Alert tone="error">{error}</Alert>
        </div>
      )}
      {rescheduleOpen && (
        <Modal title={`Reschedule · ${interview.full_name}`} onClose={() => setRescheduleOpen(false)}>
          <form onSubmit={reschedule} className="space-y-4">
            <div className="grid gap-3 md:grid-cols-3">
              <Field label="New date">
                <Input type="date" value={date} onChange={(event) => setDate(event.target.value)} />
              </Field>
              <Field label="New start time">
                <Input type="time" value={time} onChange={(event) => setTime(event.target.value)} />
              </Field>
              <Field label="New duration (minutes)">
                <Input
                  type="number"
                  min={5}
                  max={60}
                  step={5}
                  value={duration}
                  onChange={(event) => setDuration(Number(event.target.value))}
                />
              </Field>
            </div>
            <p className="text-xs text-ink-500">
              Saving invalidates the old invitation. A new link is created only when you click Send invitation again.
            </p>
            <Button type="submit">Save new slot</Button>
          </form>
        </Modal>
      )}
    </Card>
  );
}

export function Schedule() {
  const [templates, setTemplates] = useState<InterviewTemplate[]>([]);
  const [templateId, setTemplateId] = useState("");
  const [templateError, setTemplateError] = useState("");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [jobId, setJobId] = useState<number | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [runCompleted, setRunCompleted] = useState(false);
  const [approved, setApproved] = useState<ApprovedCandidate[]>([]);
  const [interviews, setInterviews] = useState<Interview[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<number | null>(null);
  const [date, setDate] = useState(tomorrow());
  const [time, setTime] = useState("10:00");
  const [duration, setDuration] = useState(30);
  const [terminateOnInterruption, setTerminateOnInterruption] = useState(false);
  const [warningLimit, setWarningLimit] = useState(3);
  const [saving, setSaving] = useState(false);
  const [schedulingMode, setSchedulingMode] = useState<"manual" | "automatic">("manual");
  const [automaticBusy, setAutomaticBusy] = useState(false);
  const loadGeneration = useRef(0);

  useEffect(() => {
    api.templates().then(setTemplates).catch(() => setTemplateError("Custom templates could not be loaded. Standard interview remains available; refresh to retry."));
  }, []);

  async function load(selectedJob: number) {
    const generation = ++loadGeneration.current;
    const [loadedInterviews, shortlist] = await Promise.all([
      api.interviews(selectedJob),
      api.latestShortlist(selectedJob),
    ]);
    const loadedApproved = shortlist.run?.status === "completed" ? await api.approvedCandidates(shortlist.run.run_id) : [];
    if (generation !== loadGeneration.current) return;
    setInterviews(loadedInterviews);
    if (shortlist.run && shortlist.run.status === "completed") {
      setRunId(shortlist.run.run_id);
      setRunCompleted(true);
      setApproved(loadedApproved);
    } else {
      setRunId(null);
      setRunCompleted(false);
      setApproved([]);
    }
  }

  useEffect(() => {
    api
      .jobs()
      .then(async (loaded) => {
        setJobs(loaded);
        if (loaded.length > 0) {
          setJobId(loaded[0].job_id);
          await load(loaded[0].job_id);
        }
      })
      .catch((err: unknown) =>
        setError(err instanceof ApiError ? err.message : "The schedule could not be loaded or saved. Please try again."),
      )
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function changeJob(next: number) {
    setJobId(next);
    setRunId(null); setRunCompleted(false); setApproved([]); setInterviews([]); setSelected(null);
    setSchedulingMode("manual");
    setError(null);
    try {
      await load(next);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The schedule could not be loaded or saved. Please try again.");
    }
  }

  async function saveSlot(event: React.FormEvent) {
    event.preventDefault();
    if (!runId || selected == null) return;
    setError(null);
    setNotice(null);
    setSaving(true);
    try {
      await api.scheduleInterview(runId, selected, combineLocal(date, time), duration, terminateOnInterruption, templates.find(item => item.template_id === templateId), terminateOnInterruption ? warningLimit : 3);
      setNotice("Interview slot saved. Review it below and click Send invitation when ready.");
      if (jobId != null) await load(jobId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The schedule could not be loaded or saved. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <Spinner label="Loading schedule…" />;
  if (jobs.length === 0) return <Alert tone="info">Create a job and approve applicants before scheduling interviews.</Alert>;

  const scheduledIds = new Set(interviews.map((item) => item.application_id));
  const approvedIds = new Set(approved.map((item) => item.application_id));
  const pending = approved.filter((item) => !scheduledIds.has(item.application_id));
  const selectedTemplate = templates.find(item => item.template_id === templateId);

  return (
    <div>
      <PageTitle
        title="Interview scheduling"
        caption="Assign separate slots to approved applicants, then approve each invitation for sending."
      />
      {notice && (
        <div className="mb-4">
          <Alert tone="success">{notice}</Alert>
        </div>
      )}
      {error && (
        <div className="mb-4">
          <Alert tone="error">{error}</Alert>
        </div>
      )}
      <Card>
        <Field label="Select a job">
          <Select disabled={automaticBusy || saving} value={jobId ?? ""} onChange={(event) => void changeJob(Number(event.target.value))}>
            {jobs.map((job) => (
              <option key={job.job_id} value={job.job_id}>
                #{job.job_id} · {job.title}
              </option>
            ))}
          </Select>
        </Field>
      </Card>

      {runCompleted && pending.length > 0 && <div className="mt-4 flex flex-wrap gap-2" role="group" aria-label="Scheduling method">
        <Button variant={schedulingMode === "manual" ? "primary" : "secondary"} disabled={automaticBusy || saving} aria-pressed={schedulingMode === "manual"} onClick={() => setSchedulingMode("manual")}>Manual scheduling</Button>
        <Button variant={schedulingMode === "automatic" ? "primary" : "secondary"} disabled={automaticBusy || saving} aria-pressed={schedulingMode === "automatic"} onClick={() => setSchedulingMode("automatic")}>Automatic scheduling by agent</Button>
      </div>}

      {runCompleted ? (
        pending.length > 0 ? (
          schedulingMode === "automatic" && runId ? <AutomaticSchedulePanel
            key={runId} runId={runId} candidates={pending} templates={templates} templateError={templateError}
            onBusyChange={setAutomaticBusy}
            onSaved={async count => {
              setNotice(`${count} interview slot(s) scheduled automatically. Review them below and send invitations when ready.`);
              if (jobId != null) await load(jobId);
            }}
          /> :
          <Card className="mt-4">
            <h2 className="mb-3 text-lg font-semibold">Schedule a candidate</h2>
            <form onSubmit={saveSlot} className="space-y-4">
              <Field label="Interview template" hint="The selected version is pinned to this interview, including when it is rescheduled.">
                <Select value={templateId} onChange={event => setTemplateId(event.target.value)}>
                  <option value="">Standard technical interview · 6 main / 4 follow-ups</option>
                  {templates.map(item => <option key={item.template_id!} value={item.template_id!}>{item.name} · v{item.version} · {MODE_LABELS[item.mode ?? 'ai']} · {item.main_questions} main / {item.max_followups} follow-ups</option>)}
                </Select>
              </Field>
              {selectedTemplate && <p className="text-xs text-ink-500">{selectedTemplate.questions?.filter(item => item.kind === 'must_ask').length ?? 0} must-ask questions · Questions and answer guidance are frozen at version {selectedTemplate.version}.</p>}
              {selectedTemplate?.recommended_duration_minutes && <p className="text-xs text-ink-500">Suggested duration: {selectedTemplate.recommended_duration_minutes} minutes. Set your preferred duration below.</p>}
              {templateError && <Alert tone="warning">{templateError}</Alert>}
              <Field label="Candidate">
                <Select value={selected ?? ""} onChange={(event) => setSelected(Number(event.target.value))}>
                  <option value="">Choose a candidate</option>
                  {pending.map((item) => (
                    <option key={item.application_id} value={item.application_id}>
                      {item.full_name} · {item.email} · #{item.application_id}
                    </option>
                  ))}
                </Select>
              </Field>
              <div className="grid gap-3 md:grid-cols-3">
                <Field label="Interview date">
                  <Input type="date" value={date} onChange={(event) => setDate(event.target.value)} />
                </Field>
                <Field label="Start time">
                  <Input type="time" value={time} onChange={(event) => setTime(event.target.value)} />
                </Field>
                <Field label="Duration (minutes)">
                  <Input
                    type="number"
                    min={5}
                    max={60}
                    step={5}
                    value={duration}
                    onChange={(event) => setDuration(Number(event.target.value))}
                  />
                </Field>
              </div>
              <p className="text-xs text-ink-500">
                Saved slots reserve the buffered start window and full interview duration. Invitations are sent only when
                you click Send invitation.
              </p>
              <label className="flex cursor-pointer items-start gap-2 text-sm">
                <Input
                  type="checkbox"
                  className="mt-0.5 w-auto"
                  checked={terminateOnInterruption}
                  onChange={(event) => setTerminateOnInterruption(event.target.checked)}
                />
                <span>
                  End the interview automatically when the allowed warning count is exceeded, or an absence/fullscreen pause remains unresolved for 60 seconds.
                </span>
              </label>
              {terminateOnInterruption && <div className="max-w-md"><Field label="Allowed warnings" hint="Fullscreen and face-absence warnings are counted together. Camera failures are recorded but do not count."><Input type="number" min={0} max={20} step={1} required value={Number.isNaN(warningLimit) ? '' : warningLimit} onChange={event => setWarningLimit(event.target.valueAsNumber)} /></Field><p className="mt-2 text-xs text-ink-500">{warningLimit === 0 ? 'The first counted warning ends the interview.' : `${warningLimit} warnings are allowed; warning ${warningLimit + 1} ends the interview. Returning to normal or refreshing does not reset this count.`}</p></div>}
              <Button type="submit" loading={saving}>
                Save slot
              </Button>
            </form>
          </Card>
        ) : approved.length > 0 ? (
          <p className="mt-4 text-sm text-ink-500">
            All approved candidates have a slot. Use Reschedule below to change an individual interview.
          </p>
        ) : (
          <div className="mt-4">
            <Alert tone="info">No applicants were approved in the latest shortlist.</Alert>
          </div>
        )
      ) : (
        <div className="mt-4">
          <Alert tone="info">Complete HR approval in Shortlist review before scheduling this job's applicants.</Alert>
        </div>
      )}

      {interviews.length > 0 && (
        <div className="mt-6 space-y-3">
          <h2 className="text-lg font-semibold">Scheduled interviews</h2>
          <Badge tone="neutral">{interviews.length} scheduled interview(s)</Badge>
          {interviews.map((interview) => (
            <InterviewRow
              key={interview.interview_id}
              interview={interview}
              approvedIds={approvedIds}
              onChanged={() => {
                if (jobId != null) void changeJob(jobId);
              }}
            />
          ))}
        </div>
      )}
    </div>
  );
}
