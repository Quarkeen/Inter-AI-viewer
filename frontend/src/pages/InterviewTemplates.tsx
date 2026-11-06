import { useEffect, useState } from "react";
import { Plus, Layers } from "lucide-react";
import { api, ApiError, type InterviewTemplate } from "../lib/api";
import { MODE_LABELS } from "../lib/interviewTemplates";
import { InterviewTemplateEditor } from "../components/InterviewTemplateEditor";
import { Alert, Badge, Button, Card, Modal, PageTitle, Spinner } from "../components/ui";

export function InterviewTemplates() {
  const [templates, setTemplates] = useState<InterviewTemplate[]>([]);
  const [history, setHistory] = useState<InterviewTemplate[] | null>(null);
  const [preview, setPreview] = useState<InterviewTemplate | null>(null);
  const [editing, setEditing] = useState<InterviewTemplate | "new" | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const message = (err: unknown) => err instanceof ApiError ? err.message : "Templates could not be loaded. Please retry.";
  useEffect(() => {
    let active = true;
    api.templates().then(values => { if (active) setTemplates(values); }).catch(err => { if (active) setError(message(err)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  function saved(result: InterviewTemplate) {
    setTemplates(items => [...items.filter(item => item.template_id !== result.template_id), result]);
    setEditing(null); setError("");
    setNotice(`Published ${result.name} · Version ${result.version}. Select it when scheduling an interview.`);
  }
  if (editing) return <InterviewTemplateEditor template={editing === "new" ? null : editing} onCancel={() => setEditing(null)} onSaved={saved} />;
  const ordered = [...templates].sort((a, b) => Number(!!b.is_preset) - Number(!!a.is_preset) || a.name.localeCompare(b.name));

  return <div>
    <PageTitle title="Interview templates" caption="Define the questions, topics and answer guidance, then assign a version when scheduling." />
    {notice && <div className="mb-4" role="status"><Alert tone="success">{notice}</Alert></div>}
    {error && <div className="mb-4"><Alert tone="error">{error}</Alert></div>}
    <div className="mb-5 flex flex-wrap items-center justify-between gap-3"><p className="text-sm text-ink-500">Custom, AI-generated or hybrid · Up to 20 questions in total</p><Button onClick={() => { setError(""); setEditing("new"); }}><Plus size={16} aria-hidden />New template</Button></div>
    <Card className="mb-4"><div className="flex items-center gap-3"><Layers size={20} className="text-brand-700" aria-hidden /><div><p className="font-semibold">Standard technical interview</p><p className="text-sm text-ink-500">6 main questions · Up to 4 follow-ups · AI-generated · Available by default</p></div></div></Card>
    {loading ? <Spinner label="Loading templates…" /> : templates.length === 0 ? <Card className="py-9 text-center"><p className="font-semibold">Build your first interview template</p><p className="mt-2 text-sm text-ink-500">Start with mandatory questions, selected topics, or your own complete question set.</p></Card> : <div className="space-y-3">{ordered.map(template => <Card key={template.template_id!}>
      <div className="flex flex-wrap items-center gap-4"><div className="min-w-0 flex-1 basis-64 [overflow-wrap:anywhere]"><div className="flex flex-wrap items-center gap-2"><p className="font-semibold">{template.name}</p>{template.is_preset && <Badge tone="brand">Admissions</Badge>}</div><p className="mt-1 text-sm text-ink-500">{template.main_questions} main questions · {template.max_followups ? `Up to ${template.max_followups} follow-ups` : "No follow-ups"} · {MODE_LABELS[template.mode ?? "ai"]}</p>{!!template.questions?.length && <p className="mt-1 text-xs text-ink-500">{template.questions.filter(item => item.kind === "must_ask").length} must-ask · {template.questions.filter(item => item.kind === "sample").length} sample questions{template.recommended_duration_minutes ? ` · Suggested slot: ${template.recommended_duration_minutes} minutes` : ''}</p>}</div><Badge tone="neutral">Version {template.version}</Badge>{!!template.questions?.length && <Button variant="secondary" onClick={() => setPreview(template)}>View questions</Button>}<Button variant="ghost" onClick={() => { setError(""); void api.templateVersions(template.template_id!).then(setHistory).catch(err => setError(message(err))); }}>History</Button><Button variant="secondary" onClick={() => { setError(""); setEditing(template); }}>Create new version</Button></div>
    </Card>)}</div>}
    {preview && <Modal title={preview.name} onClose={() => setPreview(null)}><p className="mb-3 text-sm text-ink-500">Version {preview.version} · {preview.main_questions} main questions · Up to {preview.max_followups} follow-ups{preview.recommended_duration_minutes ? ` · Suggested slot: ${preview.recommended_duration_minutes} minutes` : ''}</p>{preview.preset_description && <p className="mb-4 rounded-lg bg-slate-50 p-3 text-sm text-ink-500">{preview.preset_description}</p>}<ol className="space-y-3">{preview.questions?.map((question, index) => <li key={question.id}><details className="rounded-lg border border-slate-200 p-4"><summary className="cursor-pointer text-sm font-medium">{index + 1}. {question.question}</summary><div className="mt-3 space-y-3 text-sm"><p className="text-xs text-ink-500">{question.kind === 'must_ask' ? 'Must ask' : 'Sample'}{question.topic ? ` · ${question.topic}` : ''}</p>{question.expected_points && <div><p className="font-medium">Expected key points</p><p className="mt-1 whitespace-pre-wrap leading-relaxed">{question.expected_points}</p></div>}{question.example_answer && <div><p className="font-medium">Example answer</p><p className="mt-1 whitespace-pre-wrap leading-relaxed text-ink-500">{question.example_answer}</p></div>}{question.followup_guidance && <div><p className="font-medium">Follow-up guidance</p><p className="mt-1 whitespace-pre-wrap leading-relaxed text-ink-500">{question.followup_guidance}</p></div>}</div></details></li>)}</ol></Modal>}
    {history && <Modal title="Version history" onClose={() => setHistory(null)}><div className="space-y-3">{history.map(item => <div key={item.version} className="rounded-lg border border-slate-200 p-4"><p className="font-medium">{item.name} · Version {item.version}</p><p className="mt-1 text-sm text-ink-500">{MODE_LABELS[item.mode ?? "ai"]} · {item.main_questions} main · Up to {item.max_followups} follow-ups</p><details className="mt-3 text-sm"><summary className="cursor-pointer text-brand-700">View this version</summary><div className="mt-3 space-y-3">{!!item.topics?.length && <p>Topics: {item.topics.join(", ")}</p>}{item.questions?.length ? item.questions.map((question, index) => <div key={question.id} className="border-l-2 border-slate-200 pl-3"><p className="font-medium">{index + 1}. {question.question}</p><p className="mt-1 text-xs text-ink-500">{question.kind === "must_ask" ? "Must ask" : "Sample"}{question.topic ? ` · ${question.topic}` : ""}</p>{question.expected_points && <p className="mt-2 whitespace-pre-wrap text-sm">{question.expected_points}</p>}{question.example_answer && <p className="mt-2 whitespace-pre-wrap text-sm text-ink-500">Example: {question.example_answer}</p>}{question.followup_guidance && <p className="mt-2 whitespace-pre-wrap text-sm text-ink-500">Follow-up: {question.followup_guidance}</p>}</div>) : <p className="text-ink-500">AI-generated questions using the existing JD and candidate context.</p>}</div></details></div>)}</div></Modal>}
  </div>;
}
