const BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, init);
  if (!response.ok) {
    let detail = `Request failed (${response.status}).`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string" && body.detail) detail = body.detail;
    } catch {
      /* keep default */
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

async function download(path: string, init?: RequestInit): Promise<{ blob: Blob; filename: string }> {
  const response = await fetch(`${BASE}${path}`, init);
  if (!response.ok) {
    let detail = `Request failed (${response.status}).`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string" && body.detail) detail = body.detail;
    } catch {
      /* keep default */
    }
    throw new ApiError(response.status, detail);
  }
  const blob = await response.blob();
  const disposition = response.headers.get("content-disposition") ?? "";
  const match = disposition.match(/filename="([^"]+)"/);
  return { blob, filename: match ? match[1] : "download" };
}

export function saveDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/* ---------- shared types (mirror the backend dicts) ---------- */

export interface Job {
  job_id: number;
  title: string;
  description: string;
  created_at: string;
}

export interface Application {
  application_id: number;
  candidate_id: number;
  job_id: number;
  full_name: string;
  email: string;
  resume_filename: string;
  resume_text: string;
  job_title_at_application: string;
  jd_at_application: string;
  applied_at: string;
  current_job_title: string;
}

export interface ResumeAssessment {
  components: Record<
    string,
    { score: number; applicable: boolean; explanation: string; evidence: { resume_quote: string; jd_quote: string }[] }
  >;
  gaps: { jd_quote: string; explanation: string }[];
}

export interface ResumeEvaluation {
  evaluation_id: number;
  application_id: number;
  score: number;
  assessment: ResumeAssessment;
  evaluated_at: string;
}

export interface ShortlistRun {
  run_id: string;
  job_id: number;
  threshold: number;
  application_ids: number[];
  status: "evaluating" | "awaiting_approval" | "completed" | "superseded";
}

export interface ShortlistItem {
  application_id: number;
  full_name: string;
  email: string;
  score: number;
  proposed: number;
  approved: number | null;
  assessment: ResumeAssessment;
}

export interface ApprovedCandidate {
  application_id: number;
  evaluation_id: number;
  full_name: string;
  email: string;
  score: number;
}

export type InterviewMode = 'ai' | 'custom' | 'hybrid';

export interface TemplateQuestion {
  id: string;
  kind: 'must_ask' | 'sample';
  question: string;
  topic: string;
  expected_points: string;
  example_answer: string;
  followup_guidance: string;
}

export interface InterviewTemplate {
  template_id: string | null;
  version: number;
  name: string;
  main_questions: number;
  max_followups: number;
  mode?: InterviewMode;
  topics?: string[];
  questions?: TemplateQuestion[];
  is_preset?: boolean;
  recommended_duration_minutes?: number;
  preset_description?: string;
}

export type InterviewTemplateInput = Pick<InterviewTemplate, 'name' | 'main_questions' | 'max_followups' | 'mode' | 'topics' | 'questions'> & { expected_version?: number };

export interface Interview {
  interview_id: number;
  application_id: number;
  full_name: string;
  email: string;
  job_id: number;
  job_title: string;
  starts_at: string;
  ends_at: string;
  status: "scheduled" | "sending" | "sent" | "delivery_unknown";
  is_rescheduled: number;
  terminate_on_interruption: number;
  warning_limit?: number;
  template_json?: string | null;
}

export interface AutomaticScheduleRequest {
  run_id: string;
  application_ids: number[];
  windows: { starts_at: string; ends_at: string }[];
  duration_minutes: number;
  gap_minutes: number;
  template_id: string | null;
  template_version: number | null;
  terminate_on_interruption: boolean;
  warning_limit: number;
}

export interface AutomaticSchedulePlan {
  job_id: number;
  run_id: string;
  assignments: {
    application_id: number;
    full_name: string;
    starts_at: string;
    ends_at: string;
    reserved_from: string;
    reserved_until: string;
    interview_id?: number;
  }[];
  unassigned: { application_id: number; full_name: string }[];
  skipped_application_ids: number[];
  reservation_minutes: number;
  gap_minutes: number;
  can_schedule: boolean;
  preview_hash: string;
  batch_id?: string;
}

export interface InvitationAccess {
  state: "available" | "early" | "expired" | "invalid" | "rescheduled";
  starts_at?: string;
  ends_at?: string;
  opens_at?: string;
  closes_at?: string;
  duration_minutes?: number;
}

export interface InterviewSession {
  session_id: string;
  status: "active" | "completed" | "expired" | "terminated";
  slot_end: string;
  paused_at: string | null;
  pause_reason: string | null;
  terminate_at: string | null;
  job_title: string;
  full_name: string;
}

export interface InterviewTurn {
  turn_id: number;
  sequence: number;
  kind: "main" | "follow_up";
  main_number: number;
  question: string;
  answer_text: string | null;
  answered_at: string | null;
}

export interface InterviewState {
  proctoring?: WarningPolicy | null;
  config?: Pick<InterviewTemplate, 'template_id' | 'version' | 'name' | 'main_questions' | 'max_followups'>;
  access: InvitationAccess;
  session: InterviewSession | null;
  turns: InterviewTurn[];
  completion_message: string;
}

export interface WarningPolicy {
  warning_count: number;
  warning_limit: number;
  terminate_on_interruption: boolean;
  active_warnings: Record<string, string>;
  terminated: boolean;
}

export interface WarningEvidence {
  warning_id: string;
  signal: string;
  label: string;
  recorded_at: string;
  client_at: string;
  cleared_at: string | null;
  snapshot_at: string | null;
  snapshot_note: string;
  has_snapshot: boolean;
}

export interface ShortlistedCandidate {
  application_id: number;
  job_id: number;
  full_name: string;
  email: string;
  role: string;
  interview_id: number | null;
  starts_at: string | null;
  invitation_status: string | null;
  session_id: string | null;
  session_status: string | null;
  has_answers: number;
  report_id: number | null;
  interview_status: "Done" | "Ongoing" | "Missed" | "Terminated" | "Scheduled" | "Not scheduled";
}

export interface FinishedInterview {
  session_id: string;
  application_id: number;
  full_name: string;
  role: string;
}

export interface ReportDecision {
  outcome: string;
  notes: string;
  decided_at: string;
}

/* ---------- endpoints ---------- */

export const api = {
  templates: () => request<InterviewTemplate[]>("/api/interview-templates"),
  templateVersions: (id: string) => request<InterviewTemplate[]>(`/api/interview-templates/${encodeURIComponent(id)}/versions`),
  saveTemplate: (body: InterviewTemplateInput, id?: string) =>
    request<InterviewTemplate>(id ? `/api/interview-templates/${encodeURIComponent(id)}/versions` : "/api/interview-templates", {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
    }),
  health: () => request<{ status: string }>("/api/health"),

  jobs: () => request<Job[]>("/api/jobs"),
  createJob: (title: string, description: string) =>
    request<{ job_id: number }>("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, description }),
    }),
  extractDescription: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{ text: string }>("/api/jobs/extract-description", { method: "POST", body: form });
  },

  applications: (jobId?: number | null) =>
    request<Application[]>(`/api/applications${jobId ? `?job_id=${jobId}` : ""}`),
  submitApplication: (jobId: number, fullName: string, email: string, resume: File) => {
    const form = new FormData();
    form.append("job_id", String(jobId));
    form.append("full_name", fullName);
    form.append("email", email);
    form.append("resume", resume);
    return request<{ application_id: number; receipt: string }>("/api/applications", { method: "POST", body: form });
  },
  applicationDetail: (id: number) =>
    request<{ application: Application; evaluation: ResumeEvaluation | null }>(`/api/applications/${id}`),
  evaluateApplication: (id: number) =>
    request<{ application: Application; evaluation: ResumeEvaluation | null }>(`/api/applications/${id}/evaluate`, {
      method: "POST",
    }),
  resumeFile: (id: number) => download(`/api/applications/${id}/resume`),

  weights: () => request<{ weights: Record<string, number> }>("/api/screening/weights"),
  generateShortlist: (jobId: number, threshold: number) =>
    request<{ run_id: string }>("/api/shortlists", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: jobId, threshold }),
    }),
  latestShortlist: (jobId: number) =>
    request<{ run: ShortlistRun | null; items: ShortlistItem[] }>(`/api/shortlists?job_id=${jobId}`),
  continueShortlist: (runId: string) =>
    request<{ run_id: string }>(`/api/shortlists/${runId}/continue`, { method: "POST" }),
  saveDecisions: (runId: string, decisions: { application_id: number; approved: boolean }[]) =>
    request<{ run_id: string }>(`/api/shortlists/${runId}/decisions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decisions }),
    }),

  approvedCandidates: (runId: string) =>
    request<ApprovedCandidate[]>(`/api/scheduling/approved?run_id=${encodeURIComponent(runId)}`),
  previewAutomaticSchedule: (schedule: AutomaticScheduleRequest) =>
    request<AutomaticSchedulePlan>("/api/scheduling/automatic/preview", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(schedule),
    }),
  commitAutomaticSchedule: (body: { batch_id: string; preview_hash: string; schedule: AutomaticScheduleRequest }) =>
    request<AutomaticSchedulePlan>("/api/scheduling/automatic/commit", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    }),
  interviews: (jobId?: number | null) =>
    request<Interview[]>(`/api/interviews${jobId ? `?job_id=${jobId}` : ""}`),
  scheduleInterview: (
    runId: string,
    applicationId: number,
    startsAt: string,
    durationMinutes: number,
    terminateOnInterruption = false,
    template?: InterviewTemplate,
    warningLimit = 3,
  ) =>
    request<{ interview_id: number }>("/api/interviews", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        run_id: runId,
        application_id: applicationId,
        starts_at: startsAt,
        duration_minutes: durationMinutes,
        terminate_on_interruption: terminateOnInterruption,
        warning_limit: warningLimit,
        template_id: template?.template_id ?? null,
        template_version: template?.template_id ? template.version : null,
      }),
    }),
  interviewApproval: (id: number) => request<{ ok: boolean; reason: string | null }>(`/api/interviews/${id}/approval`),
  rescheduleInterview: (id: number, startsAt: string, durationMinutes: number) =>
    request<{ interview_id: number }>(`/api/interviews/${id}/reschedule`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ starts_at: startsAt, duration_minutes: durationMinutes }),
    }),
  sendInvitation: (id: number) =>
    request<{ interview_id: number }>(`/api/interviews/${id}/send-invitation`, { method: "POST" }),
  accessWindow: (startsAt: string, endsAt: string) =>
    request<{ opens_at: string; closes_at: string; expired: boolean }>(
      `/api/scheduling/access-window?starts_at=${encodeURIComponent(startsAt)}&ends_at=${encodeURIComponent(endsAt)}`,
    ),

  invitationAccess: (token: string) =>
    request<InvitationAccess>(`/api/invitations/access?token=${encodeURIComponent(token)}`),
  verifySetup: (token: string, checks: object, camera: Blob, audio: Blob) => {
    const form = new FormData();
    form.append("token", token); form.append("checks", JSON.stringify(checks));
    form.append("camera", camera, "camera.jpg"); form.append("audio", audio, "sample.wav");
    return request<{receipt: string; expires_in_seconds: number}>("/api/interview/preflight", {method: "POST", body: form});
  },
  startSession: (token: string, receipt: string) => {
    const form = new FormData();
    form.append("token", token);
    form.append("preflight_receipt", receipt);
    return request<{ session_id: string }>("/api/interview/sessions", { method: "POST", body: form });
  },
  interviewState: (token: string) =>
    request<InterviewState>(`/api/interview/state?token=${encodeURIComponent(token)}`),
  reportEvents: (token: string, events: { kind: string; detail: object; at: string }[]) => {
    const form = new FormData();
    form.append("token", token);
    form.append("events", JSON.stringify(events));
    return request<{ saved: number; proctoring?: WarningPolicy | null }>("/api/interview/events", { method: "POST", body: form, keepalive: JSON.stringify(events).length < 50_000 });
  },
  pauseInterview: (token: string, reason: string) => {
    const form = new FormData();
    form.append("token", token);
    form.append("reason", reason);
    return request<{ paused_at: string; pause_reason: string; terminate_at: string | null }>("/api/interview/pause", {
      method: "POST",
      body: form,
    });
  },
  resumeInterview: (token: string) => {
    const form = new FormData();
    form.append("token", token);
    return request<{ slot_end: string; credited_seconds: number }>("/api/interview/resume", { method: "POST", body: form });
  },
  terminateInterview: (token: string) => {
    const form = new FormData();
    form.append("token", token);
    return request<{ terminated: boolean; remaining_seconds: number }>("/api/interview/terminate", {
      method: "POST",
      body: form,
    });
  },
  advanceInterview: (token: string, turnId?: number | null, audio?: Blob | null) => {
    const form = new FormData();
    form.append("token", token);
    if (turnId != null) form.append("turn_id", String(turnId));
    if (audio) form.append("audio", audio, "answer.wav");
    return request<InterviewState>("/api/interview/advance", { method: "POST", body: form });
  },

  finishedInterviews: () => request<FinishedInterview[]>("/api/reports/finished"),
  reportState: (sessionId: string) =>
    request<{ source: any; report: any; current: boolean; job: { state: string; message?: string }; generating: boolean; warnings?: WarningEvidence[] }>(
      `/api/reports/state?session_id=${encodeURIComponent(sessionId)}`,
    ),
  generateReport: (sessionId: string) => {
    const form = new FormData();
    form.append("session_id", sessionId);
    return request<{ session_id: string }>("/api/reports/generate", { method: "POST", body: form });
  },
  reportDetail: (id: number) => request<{ report: any; decision: ReportDecision | null }>(`/api/reports/${id}`),
  reportPdf: (id: number) => download(`/api/reports/${id}/pdf`),
  saveReportDecision: (id: number, outcome: string, notes: string) =>
    request<{ decision: ReportDecision }>(`/api/reports/${id}/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ outcome, notes }),
    }),
  reportAudioUrl: (sessionId: string, turnId: number) =>
    `${BASE}/api/reports/audio?session_id=${encodeURIComponent(sessionId)}&turn_id=${turnId}`,
  warningSnapshotUrl: (sessionId: string, warningId: string) =>
    `${BASE}/api/reports/warning-snapshot?session_id=${encodeURIComponent(sessionId)}&warning_id=${encodeURIComponent(warningId)}`,
  shortlisted: () => request<ShortlistedCandidate[]>("/api/shortlisted"),
};
