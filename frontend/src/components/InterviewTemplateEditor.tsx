import { useEffect, useRef, useState } from "react";
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, Check, Plus, Trash2, X } from "lucide-react";
import { ApiError, api, type InterviewMode, type InterviewTemplate, type InterviewTemplateInput, type TemplateQuestion } from "../lib/api";
import { MODE_LABELS, newQuestion, templateIssues } from "../lib/interviewTemplates";
import { Alert, Badge, Button, Card, Field, Input, PageTitle, Select, Textarea, cn } from "./ui";

const STEPS = ["Details", "Questions", "Review"];
const MODES: { mode: InterviewMode; description: string }[] = [
  { mode: "hybrid", description: "Your must-ask questions first; AI fills the remaining slots." },
  { mode: "custom", description: "Supply every main question in the order it should be asked." },
  { mode: "ai", description: "AI creates the main questions from topics, samples and the existing JD." },
];

export function InterviewTemplateEditor({ template, onCancel, onSaved }: {
  template: InterviewTemplate | null; onCancel: () => void; onSaved: (value: InterviewTemplate) => void;
}) {
  const [step, setStep] = useState(0);
  const [name, setName] = useState(template?.name ?? "");
  const [mode, setMode] = useState<InterviewMode>(template?.mode ?? (template ? "ai" : "hybrid"));
  const [main, setMain] = useState(template?.main_questions ?? 6);
  const [follow, setFollow] = useState(template?.max_followups ?? 4);
  const [topics, setTopics] = useState<string[]>([...(template?.topics ?? [])]);
  const [topicInput, setTopicInput] = useState("");
  const [topicError, setTopicError] = useState("");
  const [questions, setQuestions] = useState<TemplateQuestion[]>((template?.questions ?? []).map(item => ({ ...item })));
  const [selectedId, setSelectedId] = useState<string | null>(template?.questions?.[0]?.id ?? null);
  const [issues, setIssues] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const active = questions.find(item => item.id === selectedId);
  const mustAsk = questions.filter(item => item.kind === "must_ask");
  const generated = Math.max(0, main - mustAsk.length);
  const value: InterviewTemplateInput = {
    name: name.trim(), main_questions: main, max_followups: follow, mode,
    topics: mode === "custom" ? [] : topics, questions: questions.map(item => ({ ...item,
      question: item.question.trim(), topic: item.topic.trim(), expected_points: item.expected_points.trim(),
      example_answer: item.example_answer.trim(), followup_guidance: item.followup_guidance.trim(),
    })),
    ...(template ? { expected_version: template.version } : {}),
  };
  useEffect(() => { heading.current?.focus(); }, [step]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, []);

  function updateQuestion(identity: string, change: Partial<TemplateQuestion>) {
    setQuestions(items => items.map(item => item.id === identity ? { ...item, ...change } : item));
    setIssues([]);
  }
  function addQuestion() {
    const item = newQuestion(mode === "ai" ? "sample" : "must_ask");
    setQuestions(items => [...items, item]); setSelectedId(item.id); setIssues([]);
  }
  function removeQuestion(identity: string) {
    const remaining = questions.filter(item => item.id !== identity);
    setQuestions(remaining);
    if (selectedId === identity) setSelectedId(remaining[0]?.id ?? null);
    setIssues([]);
  }
  function moveQuestion(index: number, direction: -1 | 1) {
    setQuestions(items => {
      const moved = [...items];
      [moved[index], moved[index + direction]] = [moved[index + direction], moved[index]];
      return moved;
    });
  }
  function addTopic() {
    const topic = topicInput.trim();
    if (!topic) { setTopicError("Enter a topic before adding it."); return; }
    if (topics.some(item => item.toLocaleLowerCase() === topic.toLocaleLowerCase())) { setTopicError("That topic is already selected."); return; }
    if (topics.length >= 20) { setTopicError("Choose up to 20 topics."); return; }
    setTopics(items => [...items, topic]); setTopicInput(""); setTopicError(""); setIssues([]);
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setError("");
    if (step === 1 && topicInput.trim() && mode !== "custom") {
      setIssues(["Add the topic you typed, or clear the topic field before continuing."]); return;
    }
    const found = templateIssues(value, step === 0 ? "details" : step === 1 ? "questions" : undefined);
    setIssues(found);
    if (found.length) return;
    if (step < 2) { setStep(current => current + 1); return; }
    setSaving(true);
    try {
      const saved = await api.saveTemplate(value, template?.template_id ?? undefined);
      onSaved(saved);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The template could not be saved. Your changes are still here; please retry.");
    } finally { setSaving(false); }
  }

  return <div className="mx-auto max-w-6xl">
    <Button type="button" variant="ghost" className="mb-3 -ml-4" onClick={onCancel} disabled={saving}><ArrowLeft size={16} aria-hidden />Back to templates</Button>
    <PageTitle title={template ? "Create a new template version" : "Create an interview template"} caption="Use the existing job description when scheduling. Set the questions and expectations here." />
    <ol aria-label="Template setup steps" className="mb-6 grid grid-cols-3 overflow-hidden rounded-xl border border-slate-200 bg-white">
      {STEPS.map((label, index) => <li key={label} aria-current={step === index ? "step" : undefined} className={cn("flex items-center gap-2 px-3 py-4 text-sm sm:px-5", index > 0 && "border-l border-slate-200", step === index ? "bg-brand-50 font-semibold text-brand-700" : "text-ink-500")}>
        <span className={cn("flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs", step === index ? "bg-brand-600 text-white" : "bg-slate-100")}>{index < step ? <Check size={14} aria-hidden /> : index + 1}</span>{label}
      </li>)}
    </ol>
    <form onSubmit={event => void submit(event)} noValidate>
      <fieldset disabled={saving} className="min-w-0 space-y-5">
        <h2 ref={heading} tabIndex={-1} className="text-lg font-semibold focus:outline-none">{step === 0 ? "Choose the interview format" : step === 1 ? "Build your question plan" : "Review and publish"}</h2>
        {step === 0 && <>
          <Card><div className="max-w-xl"><Field label="Template name"><Input maxLength={80} value={name} onChange={event => { setName(event.target.value); setIssues([]); }} placeholder="e.g. Junior Python · Technical interview" autoComplete="off" /></Field></div>
            <fieldset className="mt-6"><legend className="mb-3 text-sm font-medium">Interview mode</legend><div className="grid gap-3 md:grid-cols-3">{MODES.map(option => <label key={option.mode} className={cn("cursor-pointer rounded-xl border p-4", mode === option.mode ? "border-brand-600 bg-brand-50" : "border-slate-200 hover:bg-slate-50")}>
              <span className="flex items-center gap-2"><input type="radio" name="interview-mode" value={option.mode} checked={mode === option.mode} onChange={() => { setMode(option.mode); setIssues([]); }} className="accent-brand-600" /><span className="font-semibold">{MODE_LABELS[option.mode]}</span>{option.mode === "hybrid" && <span className="ml-auto text-xs text-brand-700">Recommended</span>}</span><span className="mt-2 block text-sm leading-relaxed text-ink-500">{option.description}</span>
            </label>)}</div></fieldset>
          </Card>
          <Card><h3 className="mb-4 font-semibold">Question budget</h3><div className="grid max-w-xl gap-4 sm:grid-cols-2"><Field label="Main questions"><Input type="number" min={1} max={20} step={1} value={Number.isNaN(main) ? "" : main} onChange={event => { setMain(event.target.valueAsNumber); setIssues([]); }} /></Field><Field label="Maximum follow-ups"><Input type="number" min={0} max={20} step={1} value={Number.isNaN(follow) ? "" : follow} onChange={event => { setFollow(event.target.valueAsNumber); setIssues([]); }} /></Field></div>
            <p className="mt-3 text-sm text-ink-500">Up to 20 questions in total, with at most two follow-ups per main question. Set follow-ups to 0 to disable them.</p>
          </Card>
        </>}
        {step === 1 && <>
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-5 py-4"><div className="flex flex-wrap items-center gap-2"><Badge tone="brand">{MODE_LABELS[mode]}</Badge><span className="text-sm">{mustAsk.length}/{main} must-ask · {generated} AI-generated · Up to {follow} follow-ups</span></div>{questions.length > 0 && <Button type="button" variant="secondary" onClick={addQuestion} disabled={questions.length >= 20}><Plus size={16} aria-hidden />Add {mode === "ai" ? "sample" : "question"}</Button>}</div>
          {mode !== "custom" && <Card><h3 className="font-semibold">Topics for AI-generated questions</h3><p className="mt-1 text-sm text-ink-500">Optional. Topics are prioritized in this order; the existing JD supplies the remaining context.</p>
            <div className="mt-4 flex items-end gap-2"><div className="max-w-md flex-1"><Field label="Add a topic"><Input maxLength={80} value={topicInput} placeholder="e.g. SQL transactions" onChange={event => { setTopicInput(event.target.value); setTopicError(""); }} onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); addTopic(); } }} /></Field></div><Button type="button" variant="secondary" onClick={addTopic}>Add topic</Button></div>
            {topicError && <p role="alert" className="mt-2 text-sm text-red-700">{topicError}</p>}
            {topics.length > 0 && <ul aria-label="Selected topics" className="mt-3 flex flex-wrap gap-2">{topics.map((topic, index) => <li key={topic} className="flex items-center gap-2 rounded-full bg-slate-100 px-3 py-1 text-sm"><span>{index + 1}. {topic}</span><button type="button" onClick={() => setTopics(items => items.filter(item => item !== topic))} aria-label={`Remove topic ${topic}`} className="rounded p-1 hover:bg-slate-200"><X size={13} aria-hidden /></button></li>)}</ul>}
            {topics.length > generated && <p className="mt-3 text-xs text-amber-800">There are more selected topics than AI question slots. Some topics may not be covered.</p>}
          </Card>}
          {questions.length === 0 ? <Card className="py-10 text-center"><h3 className="font-semibold">{mode === "ai" ? "Sample questions are optional" : "Add your first must-ask question"}</h3><p className="mx-auto mt-2 max-w-md text-sm text-ink-500">{mode === "ai" ? "Continue with your topics and the existing JD, or add examples to guide question style and difficulty." : "Supply the approved question and the key points a good answer should cover."}</p><Button type="button" variant="secondary" className="mt-5" onClick={addQuestion}><Plus size={16} aria-hidden />Add {mode === "ai" ? "sample" : "question"}</Button></Card> : <div className="grid items-start gap-5 lg:grid-cols-5">
            <Card className="p-3 lg:col-span-2"><h3 className="px-2 pb-3 pt-1 text-sm font-semibold">Question entries</h3><ul className="max-h-[32rem] space-y-2 overflow-y-auto pr-1">{questions.map((item, index) => <li key={item.id} className={cn("rounded-lg border", selectedId === item.id ? "border-brand-600 bg-brand-50/50" : "border-slate-200")}>
              <button type="button" aria-label={`Edit question ${index + 1}`} aria-pressed={selectedId === item.id} onClick={() => setSelectedId(item.id)} className="w-full px-3 pb-2 pt-3 text-left"><span className="mb-2 flex items-center gap-2"><span className="text-xs text-ink-500">{index + 1}</span><Badge tone={item.kind === "must_ask" ? "brand" : "neutral"}>{item.kind === "must_ask" ? "Must ask" : "Sample"}</Badge></span><span className="line-clamp-2 text-sm font-medium">{item.question || "Untitled question"}</span>{item.topic && <span className="mt-1 block truncate text-xs text-ink-500">{item.topic}</span>}</button>
              <div className="flex items-center justify-end gap-1 border-t border-slate-200/70 px-2 py-1"><Button type="button" variant="ghost" className="p-2" disabled={index === 0} aria-label={`Move question ${index + 1} up`} onClick={() => moveQuestion(index, -1)}><ArrowUp size={14} aria-hidden /></Button><Button type="button" variant="ghost" className="p-2" disabled={index === questions.length - 1} aria-label={`Move question ${index + 1} down`} onClick={() => moveQuestion(index, 1)}><ArrowDown size={14} aria-hidden /></Button><Button type="button" variant="ghost" className="p-2 text-red-700" aria-label={`Remove question ${index + 1}`} onClick={() => removeQuestion(item.id)}><Trash2 size={14} aria-hidden /></Button></div>
            </li>)}</ul><p className="px-2 pb-1 pt-4 text-xs leading-relaxed text-ink-500">Must-ask questions run first, in their relative order. Samples guide AI generation and are not guaranteed to be asked.</p></Card>
            {active && <Card key={active.id} className="space-y-5 lg:col-span-3"><div className="flex items-center justify-between"><h3 className="font-semibold">Question {questions.indexOf(active) + 1}</h3><Badge tone="neutral">Assessment guidance</Badge></div>
              <div className="grid gap-4 sm:grid-cols-2"><Field label="Question type"><Select value={active.kind} onChange={event => updateQuestion(active.id, { kind: event.target.value as TemplateQuestion["kind"] })}><option value="must_ask">Must ask</option><option value="sample">Sample</option></Select></Field><Field label="Question topic (optional)"><Input maxLength={80} value={active.topic} onChange={event => updateQuestion(active.id, { topic: event.target.value })} /></Field></div>
              <Field label="Question" hint="Up to 55 words and 500 characters. Must-ask wording is preserved."><Textarea aria-label="Question" rows={3} maxLength={500} value={active.question} onChange={event => updateQuestion(active.id, { question: event.target.value })} /></Field>
              {active.kind === "sample" && <p className="rounded-lg bg-slate-50 p-3 text-sm text-ink-500">A sample guides subject, difficulty and style. Its answer guide applies only if this exact question is selected.</p>}
              <Field label={active.kind === "must_ask" ? "Expected key points" : "Expected key points (optional)"} hint="Describe essential concepts and acceptable alternatives, not required wording. Up to 1,200 characters."><Textarea aria-label={active.kind === "must_ask" ? "Expected key points" : "Expected key points (optional)"} rows={4} maxLength={1200} value={active.expected_points} placeholder="What should a sound answer demonstrate?" onChange={event => updateQuestion(active.id, { expected_points: event.target.value })} /></Field>
              <details className="rounded-lg border border-slate-200 p-4"><summary className="cursor-pointer text-sm font-medium">Additional guidance (optional)</summary><div className="mt-4 space-y-4"><Field label="Example answer" hint="An illustrative reference, not a script to match. Up to 2,000 characters."><Textarea rows={4} maxLength={2000} value={active.example_answer} onChange={event => updateQuestion(active.id, { example_answer: event.target.value })} /></Field><Field label="Follow-up guidance" hint="Suggest an area to probe without giving away the solution. Up to 500 characters."><Textarea rows={3} maxLength={500} value={active.followup_guidance} onChange={event => updateQuestion(active.id, { followup_guidance: event.target.value })} /></Field></div></details>
            </Card>}
          </div>}
        </>}
        {step === 2 && <>
          <Card><div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="text-lg font-semibold">{name}</h3><p className="mt-1 text-sm text-ink-500">{MODE_LABELS[mode]} · {main} main questions · Up to {follow} follow-ups</p></div><Badge tone="brand">Version {(template?.version ?? 0) + 1}</Badge></div>
            <div className="mt-5 grid gap-3 sm:grid-cols-3">{[{ label: "Must ask", value: mustAsk.length }, { label: "AI-generated", value: generated }, { label: "Maximum total", value: main + follow }].map(item => <div key={item.label} className="rounded-lg bg-slate-50 p-4"><p className="text-xs text-ink-500">{item.label}</p><p className="mt-1 text-xl font-semibold">{item.value}</p></div>)}</div>
          </Card>
          {mustAsk.length > 0 && <Card><h3 className="mb-4 font-semibold">Mandatory question order</h3><ol className="divide-y divide-slate-100">{mustAsk.map((item, index) => <li key={item.id} className="flex items-start gap-3 py-3 first:pt-0 last:pb-0"><span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-50 text-xs font-medium text-brand-700">{index + 1}</span><div className="min-w-0 flex-1"><p className="text-sm font-medium">{item.question}</p>{item.topic && <p className="mt-1 text-xs text-ink-500">{item.topic}</p>}<details className="mt-2 text-sm"><summary className="cursor-pointer text-ink-500">Assessment guidance</summary><p className="mt-2 whitespace-pre-wrap text-sm">{item.expected_points}</p>{item.example_answer && <p className="mt-3 whitespace-pre-wrap text-sm text-ink-500">Example: {item.example_answer}</p>}{item.followup_guidance && <p className="mt-3 whitespace-pre-wrap text-sm text-ink-500">Follow-up: {item.followup_guidance}</p>}</details></div></li>)}</ol></Card>}
          {generated > 0 && <Card><h3 className="font-semibold">AI question plan</h3><p className="mt-2 text-sm text-ink-500">{generated} questions will be generated using {value.topics?.length ? "the selected topics and " : ""}the existing JD and candidate context{mustAsk.length ? ", after the mandatory questions" : ""}.</p>{!!value.topics?.length && <p className="mt-3 text-sm">Topic priority: {value.topics.join(" → ")}</p>}{questions.some(item => item.kind === "sample") && <details className="mt-3 text-sm"><summary className="cursor-pointer text-ink-500">{questions.filter(item => item.kind === "sample").length} sample question(s)</summary><ul className="mt-2 list-disc space-y-2 pl-5">{questions.filter(item => item.kind === "sample").map(item => <li key={item.id}>{item.question}</li>)}</ul></details>}</Card>}
          <div className="rounded-xl border border-slate-200 bg-slate-50 p-5 text-sm leading-relaxed"><p>Expected key points guide assessment. Scores keep the existing five dimensions; must-ask questions have no extra weight or automatic rejection rule.</p><p className="mt-2 text-ink-500">Mandatory questions take priority over follow-ups. Time expiry may end an interview before all questions are asked; unassessed questions are reported separately.</p>{template && <p className="mt-2 text-ink-500">Publishing creates a new version. Previously assigned interviews retain their questions and answer guidance.</p>}</div>
        </>}
        {issues.length > 0 && <div role="alert"><Alert tone="error"><ul className="list-disc space-y-1 pl-4">{issues.map(issue => <li key={issue}>{issue}</li>)}</ul></Alert></div>}
        {error && <div role="alert"><Alert tone="error">{error}</Alert></div>}
      </fieldset>
      <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 pt-5">
        <Button type="button" variant="secondary" disabled={saving} onClick={() => { if (step === 0) onCancel(); else { setStep(current => current - 1); setIssues([]); setError(""); } }}>{step === 0 ? "Cancel" : "Back"}</Button>
        <Button type="submit" loading={saving}>{step === 2 ? template ? "Publish new version" : "Publish template" : "Continue"}{step < 2 && <ArrowRight size={16} aria-hidden />}</Button>
      </div>
    </form>
  </div>;
}
