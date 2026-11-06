const ZONE = "Asia/Kolkata";
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function parts(value: string | Date) {
  const date = typeof value === "string" ? new Date(value) : value;
  const items = new Intl.DateTimeFormat("en-GB", {
    timeZone: ZONE,
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  })
    .formatToParts(date)
    .reduce<Record<string, string>>((acc, part) => ({ ...acc, [part.type]: part.value }), {});
  return { date, day: items.day, month: Number(items.month), year: items.year, hour: Number(items.hour), minute: items.minute };
}

/** Matches the backend slot format: "05 Nov 2026, 03:30 PM". */
export function formatSlot(value: string): string {
  const { day, month, year, hour, minute } = parts(value);
  const twelve = hour % 12 === 0 ? 12 : hour % 12;
  const suffix = hour < 12 ? "AM" : "PM";
  return `${day} ${MONTHS[month - 1]} ${year}, ${String(twelve).padStart(2, "0")}:${minute} ${suffix}`;
}

export function formatDate(value: string): string {
  const { day, month, year } = parts(value);
  return `${day} ${MONTHS[month - 1]} ${year}`;
}

/** Matches Python's math.floor(score + 0.5) display rounding. */
export function roundedScore(value: number | null | undefined): number | null {
  if (value == null) return null;
  return Math.floor(value + 0.5);
}

export function scoreText(value: number | null | undefined): string {
  const rounded = roundedScore(value);
  return rounded == null ? "Not assessed" : `${rounded}/100`;
}

export function initials(name: string): string {
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0] ?? "")
    .join("")
    .toUpperCase();
}

export function brief(text: string, limit = 160): string {
  const clean = text.split(/\s+/).join(" ");
  if (clean.length <= limit) return clean;
  const cut = clean.slice(0, limit - 3);
  return `${cut.slice(0, cut.lastIndexOf(" ") > 0 ? cut.lastIndexOf(" ") : cut.length)}...`;
}

/** Build an ISO timestamp with an explicit offset from local date/time inputs. */
export function combineLocal(date: string, time: string): string {
  const local = new Date(`${date}T${time}:00`);
  return local.toISOString();
}
