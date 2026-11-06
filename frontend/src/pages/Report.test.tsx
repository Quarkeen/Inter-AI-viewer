// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { api } from "../lib/api";
import { Report } from "./Report";

vi.mock("../lib/api", async original => {
  const actual = await original<typeof import("../lib/api")>();
  return { ...actual, api: { ...actual.api, finishedInterviews: vi.fn(), reportState: vi.fn(), reportDetail: vi.fn() } };
});
const question = "Explain Python generators.";
const secondQuestion = "Explain database transactions.";
function payload(guided = true): Awaited<ReturnType<typeof api.reportState>> {
  const names = ["technical_correctness", "relevance", "depth", "clarity", "role_alignment"];
  return {
    source: {
      session_id: "session", full_name: "Test Candidate", role: "Python developer", application_id: 1, interview_id: 1,
      status: "expired", started_at: "2030-01-01 10:00:00", ended_at: "2030-01-01 10:15:00",
      turns: [{ turn_id: 10, sequence: 1, kind: "main", main_number: 1, question, answer_text: "Generators yield values lazily." }],
      ...(guided ? { question_guides: { "1": { expected_points: "Lazy iteration and yield.", example_answer: "An illustrative reference answer." } } } : {}),
    },
    report: { report_id: 1, assessment: {
      overall_score: 75, main_question_count: 2, coverage: 1,
      dimension_scores: Object.fromEntries(names.map(name => [name, 75])),
      topics: [{ main_number: 1, dimensions: Object.fromEntries(names.slice(0, 4).map(name => [name, { rating: 3, score: 75, evidence: [], explanation: "Correct." }])) }],
      strengths: [], gaps: [],
      ...(guided ? { mandatory_coverage: [{ id: "one", main_number: 1, question, status: "assessed" }, { id: "two", main_number: 2, question: secondQuestion, status: "not_assessed" }] } : {}),
    } },
    current: true, job: { state: "idle" }, generating: false,
  };
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.finishedInterviews).mockResolvedValue([{ session_id: "session", full_name: "Test Candidate", role: "Python developer", application_id: 1 }]);
  vi.mocked(api.reportState).mockResolvedValue(payload());
  vi.mocked(api.reportDetail).mockResolvedValue({ report: {}, decision: null });
});
afterEach(cleanup);
function openReport() { render(<MemoryRouter initialEntries={["/candidate-report?session=session"]}><Report /></MemoryRouter>); }

it("shows incomplete mandatory coverage separately and reveals guidance only for the selected question", async () => {
  openReport();
  await screen.findByText("Mandatory questions not assessed (1)");
  expect(screen.queryByText("Lazy iteration and yield.")).toBeNull();
  fireEvent.click(screen.getByText("Mandatory questions not assessed (1)"));
  expect(screen.getByText(`${secondQuestion} · Not assessed`)).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: "Answers & evidence" }));
  fireEvent.change(screen.getByLabelText("Question"), { target: { value: "10" } });
  fireEvent.click(screen.getByText("Assessment guidance"));
  expect(screen.getByText("Lazy iteration and yield.")).toBeTruthy();
  expect(screen.getByText("Generators yield values lazily.")).toBeTruthy();
  expect(document.querySelector("audio")?.getAttribute("src")).toContain("turn_id=10");
});

it("renders an older report without template guidance or mandatory coverage", async () => {
  vi.mocked(api.reportState).mockResolvedValue(payload(false));
  openReport(); await screen.findByText("Report ready. Please record an HR decision.");
  expect(screen.queryByText(/Mandatory questions not assessed/)).toBeNull();
  fireEvent.click(screen.getByRole("tab", { name: "Answers & evidence" }));
  fireEvent.change(screen.getByLabelText("Question"), { target: { value: "10" } });
  expect(screen.queryByText("Assessment guidance")).toBeNull();
  expect(screen.getByText("Generators yield values lazily.")).toBeTruthy();
});

it('shows timestamped warning snapshots and honest missing-image placeholders in Integrity', async () => {
  const state=payload();
  state.warnings=[
    {warning_id:'warning-one',signal:'face',label:'Face not visible',recorded_at:'2030-01-01T10:01:00Z',client_at:'2030-01-01T10:00:59Z',cleared_at:null,snapshot_at:'2030-01-01T10:00:59Z',snapshot_note:'',has_snapshot:true},
    {warning_id:'warning-two',signal:'camera',label:'Camera unavailable',recorded_at:'2030-01-01T10:02:00Z',client_at:'2030-01-01T10:02:00Z',cleared_at:null,snapshot_at:null,snapshot_note:'Camera frame unavailable at warning time.',has_snapshot:false},
    {warning_id:'warning-three',signal:'gaze',label:'Sustained look-away estimate (review only)',recorded_at:'2030-01-01T10:03:00Z',client_at:'2030-01-01T10:03:00Z',cleared_at:null,snapshot_at:null,snapshot_note:'Camera frame unavailable at warning time.',has_snapshot:false},
  ];
  vi.mocked(api.reportState).mockResolvedValue(state);
  openReport(); await screen.findByText('Report ready. Please record an HR decision.');
  fireEvent.click(screen.getByRole('tab',{name:'Integrity'}));
  expect(screen.getByText('Warning 1 · Face not visible')).toBeTruthy();
  expect(screen.getByText('Warning 2 · Camera unavailable')).toBeTruthy();
  expect(screen.getByAltText('Camera snapshot for warning 1').getAttribute('src')).toContain('warning_id=warning-one');
  expect(screen.getAllByText('Camera frame unavailable at warning time.')).toHaveLength(2);
  expect(screen.getByText(/Webcam gaze estimate.*human review only/)).toBeTruthy();
  expect(screen.getAllByText(/Recorded:.*IST/)).toHaveLength(3);
  expect(screen.getByText('Recorded: 1 Jan 2030, 15:31:00 IST')).toBeTruthy();
  expect(screen.getByText('Event (browser): 1 Jan 2030, 15:30:59 IST')).toBeTruthy();
  expect(screen.getByText('Captured (browser): 1 Jan 2030, 15:30:59 IST')).toBeTruthy();
});
