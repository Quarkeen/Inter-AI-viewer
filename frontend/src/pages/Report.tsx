import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError, api, saveDownload, type FinishedInterview, type ReportDecision, type WarningEvidence } from "../lib/api";
import { roundedScore, scoreText } from "../lib/format";
import { Alert, Button, Card, Field, PageTitle, SegmentedControl, Select, Spinner, Textarea } from "../components/ui";

const OUTCOMES = ["Advance to next round", "Hold for review", "Do not advance"];
const DIMENSIONS: [string, string][] = [
  ["technical_correctness", "Technical correctness"],
  ["relevance", "Relevance"],
  ["depth", "Depth"],
  ["clarity", "Clarity"],
  ["role_alignment", "Role alignment"],
];

function durationText(source: any): string {
  if (!source.ended_at) return "-";
  const parse = (value: string) => new Date(`${value.replace(" ", "T")}Z`).getTime();
  const seconds = Math.max(0, Math.floor((parse(source.ended_at) - parse(source.started_at)) / 1000));
  return `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, "0")}s`;
}

function visibleFindings(assessment: any, key: string): any[] {
  if (key !== "gaps") return assessment[key];
  const partial = new Set(
    assessment.topics
      .filter((topic: any) => Object.values(topic.dimensions).some((value: any) => value.rating != null && value.rating <= 2))
      .map((topic: any) => topic.main_number),
  );
  return assessment.gaps.filter((item: any) => item.evidence.some((evidence: any) => partial.has(evidence.main_number)));
}

function overviewText(assessment: any): string {
  const available = Object.entries(assessment.dimension_scores).filter(([, value]) => value != null) as [string, number][];
  let text = `${assessment.coverage} of ${assessment.main_question_count ?? 6} main topics were assessed.`;
  if (available.length > 0) {
    const [name, value] = available.reduce((best, item) => (item[1] > best[1] ? item : best));
    const label = DIMENSIONS.find(([key]) => key === name)?.[1].toLowerCase() ?? name;
    text += ` The highest-scoring dimension was ${label} (${roundedScore(value)}/100).`;
  }
  if (assessment.strengths.length > 0) text += ` ${assessment.strengths[0].text}`;
  return text;
}

function Radar({ scores }: { scores: Record<string, number | null> }) {
  const cx = 250;
  const cy = 172;
  const radius = 101;
  const point = (index: number, scale: number) => {
    const angle = -Math.PI / 2 + (index * 2 * Math.PI) / 5;
    return `${(cx + radius * scale * Math.cos(angle)).toFixed(1)},${(cy + radius * scale * Math.sin(angle)).toFixed(1)}`;
  };
  const polygon = (scale: number) => [0, 1, 2, 3, 4].map((i) => point(i, scale)).join(" ");
  const values = DIMENSIONS.map(([name]) => roundedScore(scores[name]));
  const positions: [number, number][] = [
    [250, 35],
    [421, 147],
    [354, 306],
    [145, 306],
    [79, 147],
  ];
  return (
    <svg viewBox="0 0 500 350" role="img" aria-label="Interview dimension scores" style={{ width: "100%", maxHeight: 300 }}>
      <rect width="500" height="350" rx="12" fill="#f5f8fa" />
      {[25, 50, 75, 100].map((level) => (
        <polygon key={level} points={polygon(level / 100)} fill="none" stroke="#78909c" strokeOpacity=".25" />
      ))}
      {[0, 1, 2, 3, 4].map((i) => {
        const angle = -Math.PI / 2 + (i * 2 * Math.PI) / 5;
        return (
          <line
            key={i}
            x1={cx}
            y1={cy}
            x2={cx + radius * Math.cos(angle)}
            y2={cy + radius * Math.sin(angle)}
            stroke="#78909c"
            strokeOpacity=".25"
          />
        );
      })}
      {values.every((value) => value != null) && (
        <polygon
          points={values.map((value, i) => point(i, (value as number) / 100)).join(" ")}
          fill="#369b99"
          fillOpacity=".18"
          stroke="#38aaa1"
          strokeWidth="2.5"
        />
      )}
      {DIMENSIONS.map(([name, label], i) => (
        <g key={name}>
          <text x={positions[i][0]} y={positions[i][1]} textAnchor="middle" fill="#173649" fontSize="12">
            {name === "technical_correctness" ? "Correctness" : label}
          </text>
          <text x={positions[i][0]} y={positions[i][1] + 18} textAnchor="middle" fill="#38aaa1" fontSize="13" fontWeight="600">
            {scores[name] == null ? "N/A" : String(roundedScore(scores[name]))}
          </text>
        </g>
      ))}
    </svg>
  );
}

type Section = "Overview" | "Answers & evidence" | "Integrity" | "HR decision";

export function Report() {
  const [params] = useSearchParams();
  const requested = params.get("session");
  const [sessions, setSessions] = useState<FinishedInterview[] | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(requested);
  const [payload, setPayload] = useState<{
    source: any;
    report: any;
    current: boolean;
    job: { state: string; message?: string };
    generating: boolean;
    warnings?: WarningEvidence[];
    gaze_monitoring?: string;
  } | null>(null);
  const [decision, setDecision] = useState<ReportDecision | null>(null);
  const [section, setSection] = useState<Section>("Overview");
  const [questionId, setQuestionId] = useState<number | null>(null);
  const [outcome, setOutcome] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  useEffect(() => {
    api
      .finishedInterviews()
      .then((loaded) => {
        setSessions(loaded);
        if (requested && !loaded.some((item) => item.session_id === requested)) {
          setError("This interview report is unavailable.");
        } else if (!requested && loaded.length > 0) {
          setSessionId(loaded[0].session_id);
        }
      })
      .catch((err: unknown) =>
        setError(err instanceof ApiError ? err.message : "The report could not be loaded or saved. Please try again."),
      );
  }, [requested]);

  useEffect(() => {
    if (!sessionId) return;
    let active = true;
    setPayload(null);
    setError(null);
    api
      .reportState(sessionId)
      .then((result) => {
        if (!active) return;
        setPayload(result);
        if (result.current && result.report) {
          api
            .reportDetail(result.report.report_id)
            .then((detail) => {
              if (active) {
                setDecision(detail.decision);
                setOutcome(detail.decision?.outcome ?? "");
                setNotes(detail.decision?.notes ?? "");
              }
            })
            .catch(() => {});
        }
      })
      .catch((err: unknown) => {
        if (active)
          setError(err instanceof ApiError ? err.message : "The report could not be loaded or saved. Please try again.");
      });
    return () => {
      active = false;
    };
  }, [sessionId]);

  // Poll while generation runs, like the original 3s fragment.
  useEffect(() => {
    if (!sessionId || !payload || payload.current || !payload.generating) return;
    const timer = setInterval(() => {
      api
        .reportState(sessionId)
        .then(setPayload)
        .catch(() => {});
    }, 3000);
    return () => clearInterval(timer);
  }, [sessionId, payload]);

  const assessedTopics = useMemo(() => {
    if (!payload?.report) return new Set<number>();
    const topics = new Set<number>();
    for (const [key, values] of Object.entries<any>(payload.report.parts ?? {})) {
      if (key.startsWith("topics_")) for (const topic of values) topics.add(topic.main_number);
    }
    return topics;
  }, [payload]);

  async function generate() {
    if (!sessionId) return;
    setError(null);
    setWorking(true);
    try {
      await api.generateReport(sessionId);
      setPayload(await api.reportState(sessionId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The report could not be loaded or saved. Please try again.");
    } finally {
      setWorking(false);
    }
  }

  async function downloadPdf() {
    if (!payload?.report) return;
    setError(null);
    try {
      const file = await api.reportPdf(payload.report.report_id);
      saveDownload(file.blob, file.filename);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The report could not be downloaded.");
    }
  }

  async function saveDecision(event: React.FormEvent) {
    event.preventDefault();
    if (!payload?.report) return;
    setError(null);
    setWorking(true);
    try {
      const result = await api.saveReportDecision(payload.report.report_id, outcome, notes);
      setDecision(result.decision);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The report could not be loaded or saved. Please try again.");
    } finally {
      setWorking(false);
    }
  }

  if (error && !sessions) return <Alert tone="error">{error}</Alert>;
  if (!sessions) return <Spinner label="Loading reports…" />;
  if (sessions.length === 0)
    return (
      <div>
        <PageTitle title="Candidate report" />
        <Alert tone="info">Reports become available after an interview has finished with recorded answers.</Alert>
      </div>
    );
  if (error && !payload) return <Alert tone="error">{error}</Alert>;

  const choices = new Map(sessions.map((item) => [item.session_id, item] as const));
  const effective = sessionId && choices.has(sessionId) ? sessionId : null;

  return (
    <div className="mx-auto max-w-4xl">
      <Link to="/shortlisted" className="mb-3 inline-block text-sm font-medium text-brand-700 hover:underline">
        ← Back to shortlisted candidates
      </Link>
      {!requested && (
        <div className="mb-4">
          <Field label="Interview">
            <Select value={effective ?? ""} onChange={(event) => setSessionId(event.target.value)}>
              {sessions.map((item) => (
                <option key={item.session_id} value={item.session_id}>
                  {item.full_name} · #{item.application_id} · {item.role}
                </option>
              ))}
            </Select>
          </Field>
        </div>
      )}
      {error && (
        <div className="mb-4">
          <Alert tone="error">{error}</Alert>
        </div>
      )}
      {!payload || !effective ? (
        <Spinner label="Loading report…" />
      ) : !payload.current ? (
        <div className="space-y-3">
          {payload.generating ? (
            <>
              <Alert tone="info">
                Your report is being prepared. You can visit another page and return here; generation will continue.
              </Alert>
              <p className="text-sm text-ink-500">
                {assessedTopics.size > 0
                  ? `${assessedTopics.size} topics assessed and saved. Preparing the remaining assessment.`
                  : payload.job.state === "queued"
                    ? "Waiting for the assessment to begin."
                    : "Assessing the recorded answers. Requests are paced to respect model rate limits."}
              </p>
              <Spinner label="Generating…" />
            </>
          ) : (
            <>
              {payload.job.state === "failed" && payload.job.message && <Alert tone="error">{payload.job.message}</Alert>}
              <Alert tone="info">
                {payload.report && payload.report.status === "generating"
                  ? "Generation was interrupted. Your completed assessments are saved; resume to finish the report."
                  : "Generate the interview report to review scores, supporting evidence, and record an HR decision."}
              </Alert>
              <Button loading={working} onClick={() => void generate()}>
                {payload.report && payload.report.status === "generating" ? "Resume report generation" : "Generate interview report"}
              </Button>
            </>
          )}
        </div>
      ) : (
        <ReportReady
          source={payload.source}
          warnings={payload.warnings ?? []}
          gazeMonitoring={payload.gaze_monitoring}
          result={payload.report.assessment}
          decision={decision}
          section={section}
          setSection={setSection}
          questionId={questionId}
          setQuestionId={setQuestionId}
          outcome={outcome}
          setOutcome={setOutcome}
          notes={notes}
          setNotes={setNotes}
          working={working}
          onDownload={() => void downloadPdf()}
          onSave={saveDecision}
          sessionId={effective}
        />
      )}
    </div>
  );
}

function ReportReady(props: {
  warnings: WarningEvidence[];
  gazeMonitoring?: string;
  source: any;
  result: any;
  decision: ReportDecision | null;
  section: Section;
  setSection: (value: Section) => void;
  questionId: number | null;
  setQuestionId: (value: number | null) => void;
  outcome: string;
  setOutcome: (value: string) => void;
  notes: string;
  setNotes: (value: string) => void;
  working: boolean;
  onDownload: () => void;
  onSave: (event: React.FormEvent) => void;
  sessionId: string;
}) {
  const { source, result } = props;
  const turns: any[] = source.turns ?? [];
  const selected = turns.find((turn) => turn.turn_id === props.questionId) ?? null;
  const active = selected ?? null;

  return (
    <div>
      <PageTitle title={source.full_name} caption={`${source.role} · Application #${source.application_id} · Interview #${source.interview_id}`} />
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Card className="py-3">
          <p className="text-xs text-ink-500">Interview score</p>
          <p className="text-xl font-semibold">{scoreText(result.overall_score)}</p>
        </Card>
        <Card className="py-3">
          <p className="text-xs text-ink-500">Topics assessed</p>
          <p className="text-xl font-semibold">{result.coverage}/{result.main_question_count ?? 6}</p>
        </Card>
        <Card className="py-3">
          <p className="text-xs text-ink-500">Duration</p>
          <p className="text-xl font-semibold">{durationText(source)}</p>
        </Card>
        <Card className="flex items-center py-3">
          <Button variant="secondary" onClick={props.onDownload}>
            Download PDF
          </Button>
        </Card>
      </div>
      {source.status !== "completed" && (
        <div className="mb-4">
          <Alert tone="warning">This interview ended before completion. Untested topics are not scored.</Alert>
        </div>
      )}
      {result.mandatory_coverage?.some((item: { status: string }) => item.status !== "assessed") && (
        <details className="mb-4 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm">
          <summary className="cursor-pointer font-medium text-amber-900">Mandatory questions not assessed ({result.mandatory_coverage.filter((item: { status: string }) => item.status !== "assessed").length})</summary>
          <ul className="mt-3 list-disc space-y-2 pl-5">{result.mandatory_coverage.filter((item: { status: string }) => item.status !== "assessed").map((item: { id: string; question: string }) => <li key={item.id}>{item.question} · Not assessed</li>)}</ul>
          <p className="mt-3 text-ink-500">These questions contribute no score. Review coverage alongside the overall result.</p>
        </details>
      )}
      {!props.decision && (
        <div className="mb-4">
          <Alert tone="info">Report ready. Please record an HR decision.</Alert>
        </div>
      )}
      <SegmentedControl options={["Overview", "Answers & evidence", "Integrity", "HR decision"]} value={props.section} onChange={props.setSection} />
      <div className="mt-4">
        {props.section === 'Integrity' && <div className="space-y-4"><p className="text-sm text-ink-500">{props.warnings.length} warning(s) recorded. These observations support HR review and do not change the interview score. Recorded timestamps use server time; image capture timestamps are browser-reported.</p>{props.gazeMonitoring && <Card><p className="text-sm text-ink-500">{props.gazeMonitoring}</p></Card>}{props.warnings.length === 0 ? <Card><p className="text-sm">No warnings were recorded for this interview.</p></Card> : <div className="grid gap-4 sm:grid-cols-2">{props.warnings.map((warning, index) => <WarningCard key={warning.warning_id} warning={warning} number={index + 1} sessionId={props.sessionId} />)}</div>}</div>}
        {props.section === "Overview" && (
          <div className="space-y-4">
            <div className="mx-auto max-w-xl">
              <Radar scores={result.dimension_scores} />
            </div>
            <p className="text-sm">{overviewText(result)}</p>
            <div className="grid gap-3 md:grid-cols-2">
              {(
                [
                  ["Strengths", "strengths"],
                  ["Follow-up areas", "gaps"],
                ] as const
              ).map(([label, key]) => (
                <Card key={key}>
                  <p className="font-semibold">{label}</p>
                  {visibleFindings(result, key).length === 0 ? (
                    <p className="mt-1 text-sm text-ink-500">No additional finding within the assessed topics.</p>
                  ) : (
                    <ul className="mt-1 space-y-1 text-sm">
                      {visibleFindings(result, key)
                        .slice(0, 2)
                        .map((item: any, index: number) => (
                          <li key={index}>• {item.text}</li>
                        ))}
                    </ul>
                  )}
                </Card>
              ))}
            </div>
            <Button variant={props.decision ? "secondary" : "primary"} onClick={() => props.setSection("HR decision")}>
              Review HR decision
            </Button>
          </div>
        )}
        {props.section === "Answers & evidence" &&
          (turns.length === 0 ? (
            <Alert tone="info">No questions are available for this interview.</Alert>
          ) : (
            <div className="space-y-3">
              <Field label="Question">
                <Select
                  value={active?.turn_id ?? ""}
                  onChange={(event) => props.setQuestionId(event.target.value ? Number(event.target.value) : null)}
                >
                  <option value="">Choose a question</option>
                  {turns.map((turn) => (
                    <option key={turn.turn_id} value={turn.turn_id}>
                      Q{turn.sequence} · {turn.question}
                    </option>
                  ))}
                </Select>
              </Field>
              {active && (
                <Card>
                  <p className="text-xs text-ink-500">QUESTION</p>
                  <p className="mt-1 text-sm">{active.question}</p>
                  {active.kind === "main" && source.question_guides?.[String(active.main_number)] && (
                    <details className="mt-3 rounded-lg border border-slate-200 p-3 text-sm"><summary className="cursor-pointer font-medium">Assessment guidance</summary>
                      <p className="mt-2 whitespace-pre-wrap">{source.question_guides[String(active.main_number)].expected_points || "Use the standard interview rubric."}</p>
                      {source.question_guides[String(active.main_number)].example_answer && <p className="mt-3 whitespace-pre-wrap text-ink-500">Example: {source.question_guides[String(active.main_number)].example_answer}</p>}
                    </details>
                  )}
                  <p className="mt-3 text-xs text-ink-500">TRANSCRIBED ANSWER</p>
                  <p className="mt-1 whitespace-pre-wrap text-sm">{active.answer_text || "No answer recorded."}</p>
                  <p className="mt-3 text-xs text-ink-500">ORIGINAL AUDIO</p>
                  <audio className="mt-1 w-full" controls src={api.reportAudioUrl(props.sessionId, active.turn_id)} />
                </Card>
              )}
            </div>
          ))}
        {props.section === "HR decision" && (
          <div>
            <h2 className="text-lg font-semibold">HR decision</h2>
            <p className="mb-3 text-sm text-ink-500">Review the evidence, then choose the application's next step.</p>
            <Card>
              <form onSubmit={props.onSave} className="space-y-4">
                <Field label="Outcome">
                  <Select value={props.outcome} onChange={(event) => props.setOutcome(event.target.value)}>
                    <option value="">Choose an outcome</option>
                    {OUTCOMES.map((option) => (
                      <option key={option}>{option}</option>
                    ))}
                  </Select>
                </Field>
                <Field label="Internal note (optional)">
                  <Textarea value={props.notes} maxLength={1000} rows={3} onChange={(event) => props.setNotes(event.target.value)} />
                </Field>
                <Button type="submit" loading={props.working}>
                  Save HR decision
                </Button>
              </form>
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}

function WarningCard({ warning, number, sessionId }: { warning: WarningEvidence; number: number; sessionId: string }) {
  const [failed, setFailed] = useState(false);
  const stamp = (value: string) => new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium', timeStyle: 'medium', timeZone: 'Asia/Kolkata' }).format(new Date(value)) + ' IST';
  return <Card>
    <h3 className="font-semibold">Warning {number} · {warning.label}</h3>
    <p className="mt-2 text-xs text-ink-500">Recorded: {stamp(warning.recorded_at)}</p>
    <p className="mt-1 text-xs text-ink-500">Event (browser): {stamp(warning.client_at)}</p>
    {warning.has_snapshot && !failed ? <img className="mt-4 aspect-video w-full rounded-lg bg-slate-100 object-contain" src={api.warningSnapshotUrl(sessionId, warning.warning_id)} alt={`Camera snapshot for warning ${number}`} loading="lazy" onError={() => setFailed(true)} /> : <div className="mt-4 flex aspect-video items-center justify-center rounded-lg bg-slate-50 p-4 text-center text-sm text-ink-500">{failed ? 'Snapshot could not be loaded. Refresh to retry.' : warning.snapshot_note || 'Camera frame unavailable at warning time.'}</div>}
    {warning.snapshot_at && <p className="mt-2 text-xs text-ink-500">Captured (browser): {stamp(warning.snapshot_at)}</p>}
    {warning.signal === 'camera' && <p className="mt-2 text-xs text-ink-500">Equipment warning · does not consume the warning allowance.</p>}
    {warning.signal === 'gaze' && <p className="mt-2 text-xs text-ink-500">Webcam gaze estimate · for human review only. Does not change scores or consume the termination warning allowance. A snapshot does not establish cheating.</p>}
  </Card>;
}
