// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api, type ApprovedCandidate, type Interview, type Job } from "../lib/api";
import { Schedule } from "./Schedule";

vi.mock("../lib/api", async (original) => {
  const actual = await original<typeof import("../lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      jobs: vi.fn(),
      templates: vi.fn(async () => []),
      interviews: vi.fn(),
      latestShortlist: vi.fn(),
      approvedCandidates: vi.fn(),
      scheduleInterview: vi.fn(),
      interviewApproval: vi.fn(),
      accessWindow: vi.fn(),
    },
  };
});

const jobs = [{ job_id: 1, title: "Demo role" } as Job];
const shortlist = {
  run: { run_id: "run-1", status: "completed" },
  items: [],
} as unknown as Awaited<ReturnType<typeof api.latestShortlist>>;
const approved = [{ application_id: 7, full_name: "Ann Candidate", email: "ann@example.com" } as ApprovedCandidate];

function interview(overrides: Partial<Interview> = {}): Interview {
  return {
    interview_id: 1,
    application_id: 7,
    run_id: "run-1",
    evaluation_id: 1,
    starts_at: "2030-05-06T10:00:00+00:00",
    ends_at: "2030-05-06T10:30:00+00:00",
    status: "scheduled",
    full_name: "Ann Candidate",
    email: "ann@example.com",
    job_title: "Demo role",
    is_rescheduled: 0,
    terminate_on_interruption: 0,
    ...overrides,
  } as Interview;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.jobs).mockResolvedValue(jobs);
  vi.mocked(api.latestShortlist).mockResolvedValue(shortlist);
  vi.mocked(api.approvedCandidates).mockResolvedValue(approved);
  vi.mocked(api.interviews).mockResolvedValue([]);
  vi.mocked(api.interviewApproval).mockResolvedValue({ ok: true, reason: null });
  vi.mocked(api.accessWindow).mockResolvedValue({ expired: false, opens_at: "", closes_at: "" });
});
afterEach(() => {
  cleanup();
});

describe("interview auto-end setting", () => {
  it("pins the selected template version when scheduling", async () => {
    const template = { template_id: "python", version: 2, name: "Python", main_questions: 3, max_followups: 0 };
    vi.mocked(api.templates).mockResolvedValueOnce([template]);
    vi.mocked(api.scheduleInterview).mockResolvedValue({ interview_id: 1 });
    render(<Schedule />);
    await screen.findByRole("option", { name: /Python · v2/ });
    fireEvent.change(screen.getByLabelText("Candidate"), { target: { value: "7" } });
    fireEvent.change(screen.getByLabelText(/Interview template/), { target: { value: "python" } });
    fireEvent.click(screen.getByRole("button", { name: "Save slot" }));
    await waitFor(() => expect(api.scheduleInterview).toHaveBeenCalledWith("run-1", 7, expect.any(String), 30, false, template, 3));
  });

  it("submits the HR termination choice when saving a slot", async () => {
    vi.mocked(api.scheduleInterview).mockResolvedValue({ interview_id: 1 });
    render(<Schedule />);
    await screen.findByText("Schedule a candidate");
    fireEvent.change(screen.getByLabelText("Candidate"), { target: { value: "7" } });
    fireEvent.click(screen.getByRole("checkbox", { name: /End the interview automatically/ }));
    fireEvent.click(screen.getByRole("button", { name: "Save slot" }));
    await waitFor(() =>
      expect(api.scheduleInterview).toHaveBeenCalledWith("run-1", 7, expect.any(String), 30, true, undefined, 3),
    );
  });
  it("leaves automatic termination off unless HR opts in", async () => {
    vi.mocked(api.scheduleInterview).mockResolvedValue({ interview_id: 1 });
    render(<Schedule />);
    await screen.findByText("Schedule a candidate");
    fireEvent.change(screen.getByLabelText("Candidate"), { target: { value: "7" } });
    fireEvent.click(screen.getByRole("button", { name: "Save slot" }));
    await waitFor(() =>
      expect(api.scheduleInterview).toHaveBeenCalledWith("run-1", 7, expect.any(String), 30, false, undefined, 3),
    );
  });
  it("shows the auto-end setting on each saved interview", async () => {
    vi.mocked(api.interviews).mockResolvedValue([
      interview({ interview_id: 1, terminate_on_interruption: 1 }),
      interview({ interview_id: 2, application_id: 8, terminate_on_interruption: 0 }),
    ]);
    render(<Schedule />);
    expect(await screen.findByText("Auto-end on interruption: On · 3 warnings allowed")).toBeDefined();
    expect(await screen.findByText("Auto-end on interruption: Off")).toBeDefined();
  });
});

it('lets HR explicitly choose the cumulative warning allowance', async () => {
  vi.mocked(api.scheduleInterview).mockResolvedValue({interview_id:1});
  render(<Schedule />); await screen.findByText('Schedule a candidate');
  fireEvent.change(screen.getByLabelText('Candidate'),{target:{value:'7'}});
  fireEvent.click(screen.getByRole('checkbox',{name:/End the interview automatically/}));
  fireEvent.change(screen.getByLabelText(/^Allowed warnings/),{target:{value:'2'}});
  expect(screen.getByText(/warning 3 ends the interview/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'Save slot'}));
  await waitFor(()=>expect(api.scheduleInterview).toHaveBeenCalledWith('run-1',7,expect.any(String),30,true,undefined,2));
});
