import { CalendarDays, CheckCircle2, FilePlus2, Inbox, LayoutDashboard, PenLine } from "lucide-react";
import { NavLink, Outlet, useSearchParams } from "react-router-dom";
import { cn } from "./ui";

const HR_LINKS = [
  { to: "/", label: "Create a job", icon: FilePlus2 },
  { to: "/applications", label: "Applications", icon: Inbox },
  { to: "/shortlist", label: "Shortlist review", icon: CheckCircle2 },
  { to: "/interview-templates", label: "Interview templates", icon: PenLine },
  { to: "/schedule", label: "Interview scheduling", icon: CalendarDays },
  { to: "/shortlisted", label: "Shortlisted candidates", icon: LayoutDashboard },
];

const CANDIDATE_LINKS = [{ to: "/apply", label: "Apply for a job", icon: PenLine }];

function SideLink({ to, label, Icon }: { to: string; label: string; Icon: typeof Inbox }) {
  return (
    <NavLink
      to={to}
      end={to === "/"}
      className={({ isActive }) =>
        cn(
          "flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium",
          isActive ? "bg-brand-600 text-white" : "text-ink-900 hover:bg-slate-100",
        )
      }
    >
      <Icon className="h-4 w-4" aria-hidden />
      {label}
    </NavLink>
  );
}

export function AppShell() {
  const [params] = useSearchParams();
  const bare = params.get("token") != null;
  if (bare) {
    return (
      <main className="mx-auto w-full max-w-5xl px-5 py-8 md:px-8 md:py-10">
        <Outlet />
      </main>
    );
  }
  return (
    <div className="mx-auto flex min-h-screen w-full max-w-6xl gap-6 px-4 py-6">
      <aside className="w-64 shrink-0">
        <div className="sticky top-6 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <p className="px-1 pb-1 text-lg font-semibold">HR Workspace</p>
          <p className="px-1 pt-2 text-xs font-semibold uppercase tracking-wide text-ink-500">HR</p>
          <nav className="mt-1 space-y-1">
            {HR_LINKS.map((link) => (
              <SideLink key={link.to} to={link.to} label={link.label} Icon={link.icon} />
            ))}
          </nav>
          <p className="px-1 pt-4 text-xs font-semibold uppercase tracking-wide text-ink-500">Candidate</p>
          <nav className="mt-1 space-y-1">
            {CANDIDATE_LINKS.map((link) => (
              <SideLink key={link.to} to={link.to} label={link.label} Icon={link.icon} />
            ))}
          </nav>
        </div>
      </aside>
      <main className="min-w-0 flex-1">
        <Outlet />
      </main>
    </div>
  );
}
