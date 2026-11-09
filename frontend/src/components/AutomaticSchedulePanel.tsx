import { useRef, useState } from "react";
import { ApiError, api, type ApprovedCandidate, type AutomaticSchedulePlan, type AutomaticScheduleRequest, type InterviewTemplate } from "../lib/api";
import { formatSlot } from "../lib/format";
import { MODE_LABELS } from "../lib/interviewTemplates";
import { Alert, Badge, Button, Card, Field, Input, Select } from "./ui";

function nextDate() {
  const date = new Date(Date.now() + 86400000);
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit" }).format(date);
}

type WindowInput = { date: string; start: string; end: string };
type Review = { schedule: AutomaticScheduleRequest; plan: AutomaticSchedulePlan; batchId: string };

export function AutomaticSchedulePanel({ runId, candidates, templates, templateError, onSaved, onBusyChange }: {
  runId: string;
  candidates: ApprovedCandidate[];
  templates: InterviewTemplate[];
  templateError: string;
  onSaved: (count: number) => Promise<void>;
  onBusyChange: (busy: boolean) => void;
}) {
  const [windows, setWindows] = useState<WindowInput[]>([{ date: nextDate(), start: "10:00", end: "17:00" }]);
  const [duration, setDuration] = useState(30);
  const [gap, setGap] = useState(5);
  const [templateId, setTemplateId] = useState("");
  const [terminate, setTerminate] = useState(false);
  const [warningLimit, setWarningLimit] = useState(3);
  const [review, setReview] = useState<Review | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"preview" | "commit" | null>(null);
  const inFlight = useRef(false);

  function updateWindow(index: number, patch: Partial<WindowInput>) {
    setWindows(current => current.map((item, i) => i === index ? { ...item, ...patch } : item));
  }

  async function preview(event: React.FormEvent) {
    event.preventDefault();
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy("preview"); onBusyChange(true); setError(null); setReview(null);
    try {
      const template = templates.find(item => item.template_id === templateId);
      const schedule: AutomaticScheduleRequest = {
        run_id: runId, application_ids: candidates.map(item => item.application_id),
        // Window inputs are explicitly IST, independent of the recruiter's browser timezone.
        windows: windows.map(item => ({ starts_at: `${item.date}T${item.start}:00+05:30`, ends_at: `${item.date}T${item.end}:00+05:30` })),
        duration_minutes: duration, gap_minutes: gap,
        template_id: template?.template_id ?? null, template_version: template?.template_id ? template.version : null,
        terminate_on_interruption: terminate, warning_limit: terminate ? warningLimit : 3,
      };
      const plan = await api.previewAutomaticSchedule(schedule);
      setReview({ schedule, plan, batchId: crypto.randomUUID() });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The automatic schedule could not be prepared. Please retry.");
    } finally {
      inFlight.current = false; setBusy(null); onBusyChange(false);
    }
  }

  async function commit() {
    if (!review?.plan.can_schedule || inFlight.current) return;
    inFlight.current = true;
    setBusy("commit"); onBusyChange(true); setError(null);
    try {
      const saved = await api.commitAutomaticSchedule({ batch_id: review.batchId, preview_hash: review.plan.preview_hash, schedule: review.schedule });
      // The same batch ID is retained after an uncertain response, making Retry safe.
      await onSaved(saved.assignments.length);
      setReview(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The result could not be confirmed. Retry Save automatic schedule to check the same batch safely.");
      if (err instanceof ApiError && err.status === 400) setReview(null);
    } finally {
      inFlight.current = false; setBusy(null); onBusyChange(false);
    }
  }

  return <Card className="mt-4">
    <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
      <div><h2 className="text-lg font-semibold">Automatic scheduling by agent</h2>
        <p className="mt-1 text-sm text-ink-500">Assign separate slots to this job’s approved, unscheduled candidates.</p></div>
      <Badge tone="brand">{candidates.length} candidate{candidates.length === 1 ? "" : "s"}</Badge>
    </div>
    <form onSubmit={preview} onChange={() => { setReview(null); setError(null); }}>
      <fieldset disabled={busy != null} className="space-y-5 disabled:opacity-75">
        <Field label="Automatic interview template" hint="The chosen version and its question guidance are pinned to every new interview.">
          <Select value={templateId} onChange={event => setTemplateId(event.target.value)}>
            <option value="">Standard technical interview · 6 main / 4 follow-ups</option>
            {templates.map(item => <option key={item.template_id!} value={item.template_id!}>{item.name} · v{item.version} · {MODE_LABELS[item.mode ?? "ai"]} · {item.main_questions} main / {item.max_followups} follow-ups</option>)}
          </Select>
        </Field>
        {templateError && <Alert tone="warning">{templateError}</Alert>}
        <div>
          <h3 className="text-sm font-semibold">Available windows · IST</h3>
          <p className="mt-1 text-xs text-ink-500">The full reservation must fit within a window. Add a separate window for each day or either side of a break.</p>
          <div className="mt-3 space-y-3">
            {windows.map((window, index) => <div key={index} className="grid items-end gap-3 rounded-lg border border-slate-200 p-3 sm:grid-cols-[1fr_1fr_1fr_auto]">
              <Field label={`Window ${index + 1} date`}><Input type="date" required value={window.date} onChange={event => updateWindow(index, { date: event.target.value })} /></Field>
              <Field label={`Window ${index + 1} from (IST)`}><Input type="time" required value={window.start} onChange={event => updateWindow(index, { start: event.target.value })} /></Field>
              <Field label={`Window ${index + 1} until (IST)`}><Input type="time" required value={window.end} onChange={event => updateWindow(index, { end: event.target.value })} /></Field>
              <Button type="button" variant="ghost" disabled={windows.length === 1} aria-label={`Remove window ${index + 1}`} onClick={() => { setWindows(current => current.filter((_, i) => i !== index)); setReview(null); }}>Remove</Button>
            </div>)}
          </div>
          <Button type="button" variant="secondary" className="mt-3" disabled={windows.length >= 31} onClick={() => { setWindows(current => [...current, { date: current[current.length - 1].date, start: "10:00", end: "17:00" }]); setReview(null); }}>Add window</Button>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Automatic interview duration (minutes)"><Input type="number" min={5} max={60} step={1} required value={Number.isNaN(duration) ? "" : duration} onChange={event => setDuration(event.target.valueAsNumber)} /></Field>
          <Field label="Gap between reservations (minutes)"><Input type="number" min={0} max={60} step={1} required value={Number.isNaN(gap) ? "" : gap} onChange={event => setGap(event.target.valueAsNumber)} /></Field>
        </div>
        <div className="rounded-lg bg-slate-50 p-4 text-sm leading-relaxed text-ink-500">
          <p>Candidates are assigned in application order. Existing bookings, including other jobs, are kept and skipped.</p>
          <p className="mt-2">A {Number.isNaN(duration) ? "—" : duration}-minute interview reserves {Number.isNaN(duration) ? "—" : 2 * duration + 20} minutes for early/late joining and the full interview, plus the chosen gap.</p>
        </div>
        <label className="flex cursor-pointer items-start gap-2 text-sm">
          <Input type="checkbox" className="mt-0.5 w-auto" checked={terminate} onChange={event => setTerminate(event.target.checked)} />
          <span>Automatically end these interviews when the warning allowance is exceeded or an absence/fullscreen pause remains unresolved for 60 seconds.</span>
        </label>
        {terminate && <Field label="Automatic interview allowed warnings" hint="Fullscreen and face-absence warnings count together; camera failures do not count."><Input className="max-w-xs" type="number" min={0} max={20} step={1} required value={Number.isNaN(warningLimit) ? "" : warningLimit} onChange={event => setWarningLimit(event.target.valueAsNumber)} /></Field>}
        <Button type="submit" variant="secondary" loading={busy === "preview"}>Preview automatic schedule</Button>
      </fieldset>
    </form>
    {error && <div className="mt-4" role="alert"><Alert tone="error">{error}</Alert></div>}
    {review && <section aria-label="Automatic schedule preview" className="mt-6 border-t border-slate-200 pt-5">
      <h3 className="font-semibold">Review assignments · IST</h3>
      <p className="mt-1 text-sm text-ink-500">{review.plan.assignments.length} of {review.plan.assignments.length + review.plan.unassigned.length} candidates fit. Existing reservations are excluded across all jobs.</p>
      {review.plan.skipped_application_ids.length > 0 && <p className="mt-2 text-sm text-ink-500">{review.plan.skipped_application_ids.length} already scheduled candidate(s) skipped.</p>}
      {review.plan.unassigned.length > 0 && <div className="mt-3"><Alert tone="warning">Not enough capacity for {review.plan.unassigned.map(item => item.full_name).join(", ")}. Add or extend a window and preview again. No interviews have been saved.</Alert></div>}
      {review.plan.assignments.length > 0 && <div className="mt-4 overflow-x-auto rounded-lg border border-slate-200">
        <table className="w-full text-left text-sm">
          <thead className="bg-slate-50 text-xs text-ink-500"><tr><th className="px-4 py-3">Candidate</th><th className="px-4 py-3">Interview slot</th><th className="px-4 py-3">Full reserved window</th></tr></thead>
          <tbody>{review.plan.assignments.map(item => <tr key={item.application_id} className="border-t border-slate-100">
            <td className="px-4 py-3"><p className="font-medium">{item.full_name}</p><p className="text-xs text-ink-500">#{item.application_id}</p></td>
            <td className="px-4 py-3">{formatSlot(item.starts_at)}<br /><span className="text-xs text-ink-500">Until {formatSlot(item.ends_at)}</span></td>
            <td className="px-4 py-3 text-xs text-ink-500">{formatSlot(item.reserved_from)}<br />Until {formatSlot(item.reserved_until)}</td>
          </tr>)}</tbody>
        </table>
      </div>}
      <p className="mt-4 text-xs text-ink-500">Saving rechecks availability and creates all slots together. Review the saved interviews below and use Send invitation when ready.</p>
      <Button type="button" className="mt-3" loading={busy === "commit"} disabled={!review.plan.can_schedule || busy != null} onClick={() => void commit()}>Save automatic schedule</Button>
    </section>}
  </Card>;
}
