// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api, ApiError, type InterviewTemplate } from "../lib/api";
import { InterviewTemplates } from "./InterviewTemplates";

vi.mock("../lib/api", async original => {
  const actual = await original<typeof import("../lib/api")>();
  return { ...actual, api: { ...actual.api, templates: vi.fn(), saveTemplate: vi.fn(), templateVersions: vi.fn() } };
});
const template: InterviewTemplate = { template_id: "python", version: 1, name: "Python", main_questions: 3, max_followups: 0 };
beforeEach(() => { vi.clearAllMocks(); vi.mocked(api.templates).mockResolvedValue([template]); });
afterEach(cleanup);
const click = (name: string) => fireEvent.click(screen.getByRole("button", { name }));
const change = (label: string | RegExp, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
async function startNew(mode = "ai", main = "3", follow = "0") {
  render(<InterviewTemplates />); await screen.findByText("Python"); click("New template");
  change("Template name", "Python");
  fireEvent.click(screen.getByRole("radio", { name: mode === "ai" ? /^AI-generated/ : mode === "custom" ? /^Custom/ : /^Hybrid/ }));
  change("Main questions", main); change("Maximum follow-ups", follow); click("Continue");
}
function addQuestion(text: string, expected = "Correct, role-appropriate technical explanation.") {
  click("Add question"); change("Question", text); change(/^Expected key points/, expected);
}

it("publishes an AI template through Details, Questions and Review without uploading another JD", async () => {
  vi.mocked(api.saveTemplate).mockResolvedValue(template);
  await startNew();
  expect(screen.getByText("Sample questions are optional")).toBeTruthy();
  expect(document.querySelector('input[type="file"]')).toBeNull();
  click("Continue");
  expect(api.saveTemplate).not.toHaveBeenCalled();
  click("Publish template");
  await waitFor(() => expect(api.saveTemplate).toHaveBeenCalledWith({ name: "Python", main_questions: 3, max_followups: 0, mode: "ai", topics: [], questions: [] }, undefined));
  await screen.findByText(/Published Python/);
});

it("edits legacy templates as a new version and preserves version history", async () => {
  vi.mocked(api.saveTemplate).mockResolvedValue({ ...template, version: 2 });
  vi.mocked(api.templateVersions).mockResolvedValue([template]);
  render(<InterviewTemplates />); await screen.findByText("Python"); click("Create new version");
  expect((screen.getByRole("radio", { name: /^AI-generated/ }) as HTMLInputElement).checked).toBe(true);
  change("Main questions", "4"); click("Continue"); click("Continue"); click("Publish new version");
  await waitFor(() => expect(api.saveTemplate).toHaveBeenCalledWith({ name: "Python", main_questions: 4, max_followups: 0, mode: "ai", topics: [], questions: [], expected_version: 1 }, "python"));
  await screen.findByText(/Published Python/); click("History");
  await screen.findByText("Python · Version 1");
  expect(api.templateVersions).toHaveBeenCalledWith("python");
});

it("blocks a budget over twenty before advancing", async () => {
  render(<InterviewTemplates />); await screen.findByText("Python"); click("New template");
  change("Template name", "Too long"); change("Main questions", "20"); click("Continue");
  expect(screen.getByRole("alert").textContent).toContain("20 or fewer");
  expect(screen.queryByText("Build your question plan")).toBeNull();
  expect(api.saveTemplate).not.toHaveBeenCalled();
});

it("publishes a hybrid template with mandatory wording, topics and private guidance", async () => {
  vi.mocked(api.saveTemplate).mockResolvedValue({ ...template, mode: "hybrid" });
  await startNew("hybrid", "3", "2");
  change("Add a topic", "SQL transactions"); click("Add topic");
  addQuestion("How do you prevent SQL injection?", "Parameterized queries; accept a correctly used ORM.");
  change("Question topic (optional)", "Database security");
  fireEvent.click(screen.getByText("Additional guidance (optional)"));
  change(/^Example answer/, "Use driver parameters rather than concatenating untrusted SQL.");
  change(/^Follow-up guidance/, "Ask which API they use in their driver.");
  click("Continue");
  expect(screen.getByText("Mandatory question order")).toBeTruthy();
  click("Publish template");
  await waitFor(() => expect(api.saveTemplate).toHaveBeenCalled());
  const saved = vi.mocked(api.saveTemplate).mock.calls[0][0];
  expect(saved.mode).toBe("hybrid"); expect(saved.topics).toEqual(["SQL transactions"]);
  expect(saved.questions).toEqual([expect.objectContaining({ kind: "must_ask", question: "How do you prevent SQL injection?", expected_points: "Parameterized queries; accept a correctly used ORM.", example_answer: "Use driver parameters rather than concatenating untrusted SQL.", followup_guidance: "Ask which API they use in their driver." })]);
});

it("requires expected key points for mandatory questions and preserves the draft across steps", async () => {
  await startNew("hybrid"); addQuestion("Explain Python generators.", ""); click("Continue");
  expect(screen.getByRole("alert").textContent).toContain("expected key points");
  change(/^Expected key points/, "Lazy iteration and yield."); click("Back");
  expect((screen.getByLabelText("Template name") as HTMLInputElement).value).toBe("Python");
  click("Continue");
  expect((screen.getByLabelText(/^Expected key points/) as HTMLTextAreaElement).value).toBe("Lazy iteration and yield.");
  click("Continue"); expect(screen.getByText("Review and publish")).toBeTruthy();
});

it("requires every custom main question and publishes their reordered sequence", async () => {
  vi.mocked(api.saveTemplate).mockResolvedValue({ ...template, mode: "custom" });
  await startNew("custom", "2"); addQuestion("Explain transactions."); click("Continue");
  expect(screen.getByRole("alert").textContent).toContain("every main question");
  addQuestion("Explain generators."); click("Move question 2 up"); click("Continue"); click("Publish template");
  await waitFor(() => expect(api.saveTemplate).toHaveBeenCalled());
  expect(vi.mocked(api.saveTemplate).mock.calls[0][0].questions?.map(item => item.question)).toEqual(["Explain generators.", "Explain transactions."]);
});

it("keeps only one question editor open and supports removing an entry", async () => {
  await startNew("hybrid"); addQuestion("Explain transactions."); addQuestion("Explain generators.");
  expect(screen.getAllByLabelText(/^Expected key points/)).toHaveLength(1);
  click("Remove question 2");
  expect((screen.getByLabelText("Question") as HTMLTextAreaElement).value).toBe("Explain transactions.");
  expect(screen.queryByRole("button", { name: "Edit question 2" })).toBeNull();
});

it("prevents duplicate questions and duplicate topics", async () => {
  await startNew("hybrid");
  change("Add a topic", "SQL"); click("Add topic"); change("Add a topic", "sql"); click("Add topic");
  expect(screen.getByRole("alert").textContent).toContain("already selected");
  change("Add a topic", "");
  addQuestion("Explain Python."); addQuestion("explain python?"); click("Continue");
  expect(screen.getByRole("alert").textContent).toContain("duplicates another entry");
  expect(api.saveTemplate).not.toHaveBeenCalled();
});

it("supports optional samples without treating them as mandatory", async () => {
  vi.mocked(api.saveTemplate).mockResolvedValue(template);
  await startNew(); click("Add sample"); change("Question", "Describe database indexes.");
  expect((screen.getByLabelText("Question type") as HTMLSelectElement).value).toBe("sample");
  click("Continue"); click("Publish template");
  await waitFor(() => expect(api.saveTemplate).toHaveBeenCalled());
  expect(vi.mocked(api.saveTemplate).mock.calls[0][0].questions).toEqual([expect.objectContaining({ kind: "sample", expected_points: "" })]);
});

it("retains a failed save for retry instead of discarding the question plan", async () => {
  vi.mocked(api.saveTemplate).mockRejectedValueOnce(new ApiError(400, "This template changed. Refresh before saving a new version."));
  render(<InterviewTemplates />); await screen.findByText("Python"); click("Create new version");
  click("Continue"); click("Continue"); click("Publish new version");
  await screen.findByText("This template changed. Refresh before saving a new version.");
  click("Back"); expect(screen.getByText("Build your question plan")).toBeTruthy();
  click("Back"); expect((screen.getByLabelText("Template name") as HTMLInputElement).value).toBe("Python");
});

it('places the admissions preset directly after Standard and previews its question guidance', async () => {
  const admissions: InterviewTemplate = {
    template_id:'iiit-dharwad-mtech-dsai-foundations',version:1,name:'M.Tech DSAI Admissions - Foundations',
    main_questions:10,max_followups:4,mode:'custom',is_preset:true,recommended_duration_minutes:45,
    preset_description:'Proposed admissions-readiness template for faculty review.',
    questions:[{id:'dsai-background',kind:'must_ask',topic:'Motivation',question:'What would you like to learn?',expected_points:'A specific learning interest and relevant background.',example_answer:'I want to strengthen mathematical reasoning.',followup_guidance:'Ask for one specific learning goal.'}],
  };
  vi.mocked(api.templates).mockResolvedValue([{...template,name:'A first alphabetical template'},admissions]);
  render(<InterviewTemplates />);
  await screen.findByText(admissions.name);
  const standard=screen.getByText('Standard technical interview');
  const preset=screen.getByText(admissions.name);
  const custom=screen.getByText('A first alphabetical template');
  expect(standard.compareDocumentPosition(preset) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(preset.compareDocumentPosition(custom) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.getByText(/Suggested slot: 45 minutes/)).toBeTruthy();
  click('View questions');
  expect(screen.getByRole('dialog')).toBeTruthy();
  fireEvent.click(screen.getByText('1. What would you like to learn?'));
  expect(screen.getByText('A specific learning interest and relevant background.')).toBeTruthy();
  expect(screen.getByText('I want to strengthen mathematical reasoning.')).toBeTruthy();
  expect(screen.getByText('Ask for one specific learning goal.')).toBeTruthy();
  expect(api.saveTemplate).not.toHaveBeenCalled();
});
