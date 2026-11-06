import type { InterviewMode, InterviewTemplateInput, TemplateQuestion } from "./api";

export const MODE_LABELS: Record<InterviewMode, string> = {
  ai: "AI-generated", custom: "Custom", hybrid: "Hybrid",
};

export function questionKey(text: string): string {
  return text.replace(/[^\p{L}\p{N}_]/gu, "").toLocaleLowerCase();
}

export function newQuestion(kind: TemplateQuestion["kind"]): TemplateQuestion {
  return { id: crypto.randomUUID(), kind, question: "", topic: "", expected_points: "", example_answer: "", followup_guidance: "" };
}

export function templateIssues(value: InterviewTemplateInput, section?: "details" | "questions"): string[] {
  const issues: string[] = [];
  const questions = value.questions ?? [];
  const topics = value.topics ?? [];
  const required = questions.filter(item => item.kind === "must_ask");
  const checkDetails = section !== "questions";
  const checkQuestions = section !== "details";
  if (checkDetails) {
    if (!value.name.trim() || value.name.length > 80) issues.push("Enter a template name of up to 80 characters.");
    if (!Number.isInteger(value.main_questions) || value.main_questions < 1 || value.main_questions > 20) issues.push("Choose between 1 and 20 main questions.");
    if (!Number.isInteger(value.max_followups) || value.max_followups < 0 || value.max_followups > 20) issues.push("Choose between 0 and 20 follow-ups.");
    if (value.main_questions + value.max_followups > 20) issues.push("Keep the combined question budget at 20 or fewer.");
    if (value.max_followups > value.main_questions * 2) issues.push("Allow at most two follow-ups per main question.");
  }
  if (checkQuestions) {
    if (required.length > value.main_questions) issues.push("Must-ask questions cannot exceed the main-question count.");
    if (value.mode === "custom" && (required.length !== value.main_questions || questions.length !== value.main_questions)) issues.push("Custom mode needs one must-ask entry for every main question, without samples.");
    if (value.mode === "hybrid" && !required.length) issues.push("Add at least one must-ask question for a hybrid interview.");
    if (value.mode === "ai" && required.length) issues.push("Use hybrid or custom mode for must-ask questions, or change their type to Sample.");
    if (questions.length > 20) issues.push("Add at most 20 question entries.");
    if (topics.length > 20 || topics.some(topic => !topic.trim() || topic.length > 80)) issues.push("Choose up to 20 topics, each no longer than 80 characters.");
    if (new Set(topics.map(topic => topic.toLocaleLowerCase())).size !== topics.length) issues.push("Remove duplicate topics.");
    const seen = new Set<string>();
    questions.forEach((item, index) => {
      const key = questionKey(item.question);
      if (!key) issues.push(`Enter the text for question ${index + 1}.`);
      if (item.question.length > 500 || item.question.trim().split(/\s+/).length > 55) issues.push(`Keep question ${index + 1} to 55 words and 500 characters.`);
      if (key && seen.has(key)) issues.push(`Question ${index + 1} duplicates another entry.`);
      seen.add(key);
      if (item.kind === "must_ask" && !item.expected_points.trim()) issues.push(`Add expected key points for question ${index + 1}.`);
      if (item.topic.length > 80 || item.expected_points.length > 1200 || item.example_answer.length > 2000 || item.followup_guidance.length > 500) issues.push(`Shorten the guidance for question ${index + 1} to the displayed limits.`);
    });
  }
  return issues;
}
