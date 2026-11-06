/** Presentation-only cleanup. The saved source text is never rewritten. */
function sections(text: string) {
  const lines = text.replace(/\r/g, "").split("\n");
  const result: { title: string; paragraphs: string[] }[] = [];
  let current = { title: "Role overview", paragraphs: [] as string[] };
  let paragraph = "";
  function flush() { if (paragraph.trim()) current.paragraphs.push(paragraph.trim()); paragraph = ""; }
  for (const source of lines) {
    const line = source.trim();
    // Standalone page numbers and test-document headers are PDF artifacts in the display.
    if (/^\d{1,3}$/.test(line) || /^FICTIONAL SAMPLE\s*\|/i.test(line)) continue;
    if (!line) { flush(); continue; }
    const heading = line.replace(/:$/, "");
    if (heading.length <= 65 && (/^(role overview|summary|responsibilities|key responsibilities|required skills(?: & qualifications)?|requirements|preferred qualifications|preferred.*skills|education|qualifications|about the role)$/i.test(heading) || /^[A-Z][A-Z\s/&-]{3,}$/.test(heading))) {
      flush(); if (current.paragraphs.length) result.push(current);
      current = { title: heading.toLowerCase().replace(/\b\w/g, c => c.toUpperCase()), paragraphs: [] }; continue;
    }
    const bullet = /^[•\u007f▪*-]\s*/.test(line);
    if (bullet) { flush(); paragraph = line.replace(/^[•\u007f▪*-]\s*/, ""); }
    else if (paragraph && !/[.!?:]$/.test(paragraph)) paragraph += " " + line;
    else { flush(); paragraph = line; }
  }
  flush(); if (current.paragraphs.length) result.push(current);
  return result;
}
export function JobDescription({ text }: { text: string }) {
  return <details className="overflow-hidden rounded-xl border border-slate-200 bg-slate-50/40">
    <summary className="cursor-pointer px-4 py-3 text-sm font-semibold text-brand-700">View job description</summary>
    <div className="max-h-96 space-y-5 overflow-auto border-t border-slate-200 bg-white p-5">
      {text.match(/^FICTIONAL SAMPLE[^\n]*/im) && <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">Fictional sample — for capstone testing only</p>}
      {sections(text).map((section,i) => <section key={i}><h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-500">{section.title}</h3>
        <ul className="space-y-2 text-sm leading-relaxed">{section.paragraphs.map((p,index) => <li className="flex gap-2" key={index}><span className="text-brand-700" aria-hidden>•</span><span>{p}</span></li>)}</ul>
      </section>)}
    </div>
  </details>;
}
