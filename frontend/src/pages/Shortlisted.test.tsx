// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { api, type ShortlistedCandidate } from "../lib/api";
import { Shortlisted } from "./Shortlisted";

vi.mock("../lib/api", async (original) => {
  const actual = await original<typeof import("../lib/api")>();
  return { ...actual, api: { ...actual.api, shortlisted: vi.fn() } };
});

const candidate = {
  job_id: 1,
  role: "Demo role",
  application_id: 7,
  full_name: "Ann Candidate",
  email: "ann@example.com",
  interview_id: 1,
  starts_at: null,
  invitation_status: "sent",
  interview_status: "Terminated",
  session_status: "terminated",
  session_id: "session-1",
  has_answers: 1,
  report_id: null,
} as unknown as ShortlistedCandidate;

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.shortlisted).mockResolvedValue([candidate]);
});
afterEach(() => {
  cleanup();
});

describe("terminated interviews", () => {
  it("shows terminated candidates with an openable report", async () => {
    render(
      <MemoryRouter>
        <Shortlisted />
      </MemoryRouter>,
    );
    expect(await screen.findByText("⛔ Terminated")).toBeDefined();
    expect(screen.getByRole("link", { name: /Open report/ })).toBeDefined();
  });
  it("filters by the terminated status", async () => {
    render(
      <MemoryRouter>
        <Shortlisted />
      </MemoryRouter>,
    );
    await screen.findByText("⛔ Terminated");
    fireEvent.change(screen.getByLabelText("Interview status"), { target: { value: "Done" } });
    expect(await screen.findByText(/No applicants match/)).toBeDefined();
    fireEvent.change(screen.getByLabelText("Interview status"), { target: { value: "Terminated" } });
    expect(await screen.findByText(/Ann Candidate/)).toBeDefined();
  });
});

it('allows review of a terminated interview even when no answer was recorded', async () => {
  vi.mocked(api.shortlisted).mockResolvedValue([{...candidate,has_answers:0}]);
  render(<MemoryRouter><Shortlisted /></MemoryRouter>);
  expect(await screen.findByRole('link',{name:/Open report/})).toBeTruthy();
});
