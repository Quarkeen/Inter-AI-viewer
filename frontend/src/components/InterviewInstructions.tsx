import { ArrowLeft, ArrowRight, CheckCircle2, Clock3, MessageSquare, Mic } from "lucide-react";
import { Button, Card } from "./ui";
import { formatSlot } from "../lib/format";
import { GAZE_AWAY_MS } from '../lib/gaze';

export function InterviewInstructions({ duration, opensAt, closesAt, busy, onBack, onStart, mainQuestions = 6, maxFollowups = 4, warningLimit }: {
  warningLimit?: number;
  mainQuestions?: number; maxFollowups?: number; duration?: number; opensAt?: string; closesAt?: string; busy: boolean; onBack: () => void; onStart: () => void;
}) {
  return <Card className="p-0 overflow-hidden">
    <div className="border-b border-slate-200 px-6 py-6 md:px-8">
      <p className="mb-3 flex items-center gap-2 text-sm font-medium text-emerald-700"><CheckCircle2 size={17} aria-hidden />System check complete</p>
      <h2 className="text-xl font-semibold">Before you start</h2>
      <p className="mt-2 text-sm leading-relaxed text-ink-500">Take a moment to review the format. Your timer starts only when you select Start interview.</p>
    </div>
    <div className="space-y-7 p-6 md:p-8">
      <div className="grid gap-4 sm:grid-cols-3">
        {[{ Icon: Clock3, title: `${duration ?? "—"} minutes`, text: "From the moment you start" }, { Icon: MessageSquare, title: `${mainQuestions} main question${mainQuestions === 1 ? "" : "s"}`, text: maxFollowups ? `Up to ${maxFollowups} follow-up questions` : "No follow-up questions" }, { Icon: Mic, title: "Spoken answers", text: "Up to 3 minutes per answer" }].map(({ Icon, title, text }) => <div key={title} className="rounded-xl bg-slate-50 p-4"><Icon size={19} className="mb-3 text-brand-700" aria-hidden /><p className="font-semibold">{title}</p><p className="mt-1 text-xs text-ink-500">{text}</p></div>)}
      </div>
      <section><h3 className="mb-4 font-semibold">How to answer</h3><ol className="space-y-4 text-sm">
        {["Listen to or read the question. Let the interviewer finish speaking before recording.", "Record your answer, stop when finished, then submit it. Your transcript will appear automatically.", "Wait for the next question. The interviewer may ask a follow-up based on your answer."].map((text, i) => <li key={text} className="flex items-start gap-3"><span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-50 text-xs font-semibold text-brand-700">{i + 1}</span><span className="pt-0.5 leading-relaxed">{text}</span></li>)}
      </ol></section>
      <section className="rounded-xl border border-slate-200 p-4 text-sm"><h3 className="font-semibold">Interview monitoring</h3><p className="mt-2 leading-relaxed text-ink-500">Stay in fullscreen with your face visible. Warning timestamps and a camera snapshot, when available, are saved for HR review. Equipment failures are recorded separately and do not count toward the warning allowance.</p>{warningLimit != null && <p className="mt-2 leading-relaxed">{warningLimit} fullscreen/face-absence warnings are allowed; warning {warningLimit + 1} ends this interview. A continuous absence/fullscreen violation pauses after 30 seconds and ends the interview if unresolved for another 60 seconds. Returning or refreshing does not reset the warning count.</p>}</section>
      <section className="rounded-xl border border-slate-200 p-4 text-sm"><h3 className="font-semibold">Comfortable viewing area</h3><p className="mt-2 leading-relaxed text-ink-500">Calibrated eye tracking allows natural glances and uses an extra buffer around your screen. More than {GAZE_AWAY_MS / 1000} seconds of reliable look-away can produce a reminder with a snapshot for HR review. Gaze observations do not affect scores, pause the interview, or consume the termination warning allowance. Uncertain eye readings do not create new gaze warnings. Keep your camera in the position used for calibration.</p></section>
      <div className="rounded-xl border border-slate-200 p-4 text-sm"><p className="font-medium">Your allowed start window</p><p className="mt-1 text-ink-500">{opensAt ? formatSlot(opensAt) : "—"} – {closesAt ? formatSlot(closesAt) : "—"}</p><p className="mt-2 text-xs text-ink-500">Refreshing an active interview does not reset its timer. The interview may end before all questions are asked if time expires.</p></div>
    </div>
    <div className="flex flex-col gap-3 border-t border-slate-200 bg-slate-50/60 px-6 py-5 sm:flex-row sm:justify-between md:px-8">
      <Button variant="ghost" disabled={busy} onClick={onBack}><ArrowLeft size={16} aria-hidden />Back to system check</Button>
      <Button loading={busy} onClick={onStart}>Start interview<ArrowRight size={16} aria-hidden /></Button>
    </div>
  </Card>;
}
