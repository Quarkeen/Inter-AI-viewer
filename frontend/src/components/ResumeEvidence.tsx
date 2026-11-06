import { useState } from "react";
import type { ResumeAssessment } from "../lib/api";
import { brief } from "../lib/format";
import { Button } from "./ui";

/** Compact resume evidence, ported from the original shared evidence view. */
export function ResumeEvidence({ assessment }: { assessment: ResumeAssessment }) {
  const [open, setOpen] = useState(false);
  const summaries = [
    ...new Set(
      Object.values(assessment.components)
        .filter((component) => component.applicable)
        .map((component) => component.explanation),
    ),
  ];
  const gaps = assessment.gaps;
  const seen = new Set<string>();
  const pairs: { resume: string; jd: string }[] = [];
  for (const component of Object.values(assessment.components)) {
    if (!component.applicable) continue;
    for (const evidence of component.evidence) {
      const key = `${evidence.resume_quote} ${evidence.jd_quote}`.split(/\s+/).join(" ");
      if (seen.has(key)) continue;
      seen.add(key);
      pairs.push({ resume: evidence.resume_quote, jd: evidence.jd_quote });
    }
  }
  return (
    <div className="text-sm">
      <p className="font-semibold">Match summary</p>
      <ul className="mt-1 space-y-1">
        {summaries.map((summary) => (
          <li key={summary}>• {brief(summary)}</li>
        ))}
      </ul>
      {gaps.length > 0 && (
        <>
          <p className="mt-3 font-semibold">Not demonstrated</p>
          <ul className="mt-1 space-y-1">
            {[...new Set(gaps.map((gap) => gap.explanation))].slice(0, 2).map((text) => (
              <li key={text}>• {brief(text)}</li>
            ))}
          </ul>
          {gaps.length > 2 && (
            <p className="mt-1 text-xs text-ink-500">Additional gaps are included in the supporting passages.</p>
          )}
        </>
      )}
      <Button variant="secondary" className="mt-3" onClick={() => setOpen((value) => !value)}>
        {open ? "Hide supporting passages" : "Show supporting passages"}
      </Button>
      {open && (
        <div className="mt-3 space-y-3">
          {pairs.map((pair, index) => (
            <div key={index} className="grid gap-3 rounded-lg border border-slate-200 p-3 md:grid-cols-2">
              <div>
                <p className="text-xs font-medium text-ink-500">Resume</p>
                <p className="mt-1 whitespace-pre-wrap text-xs">{pair.resume}</p>
              </div>
              <div>
                <p className="text-xs font-medium text-ink-500">Job requirement</p>
                <p className="mt-1 whitespace-pre-wrap text-xs">{pair.jd}</p>
              </div>
            </div>
          ))}
          {gaps.map((gap, index) => (
            <div key={`gap-${index}`} className="rounded-lg border border-slate-200 p-3">
              <p className="text-xs font-medium text-ink-500">Not demonstrated in the resume</p>
              <p className="mt-1 text-xs">{gap.explanation}</p>
              <p className="mt-1 whitespace-pre-wrap text-xs text-ink-500">{gap.jd_quote}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
