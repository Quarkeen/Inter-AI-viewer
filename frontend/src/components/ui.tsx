import { cva, type VariantProps } from "class-variance-authority";
import { clsx, type ClassValue } from "clsx";
import { Loader2 } from "lucide-react";
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600 disabled:cursor-not-allowed disabled:opacity-50",
  {
    variants: {
      variant: {
        primary: "bg-brand-600 text-white hover:bg-brand-700",
        secondary: "border border-slate-300 bg-white text-ink-900 hover:bg-slate-50",
        ghost: "text-ink-900 hover:bg-slate-100",
        danger: "bg-red-600 text-white hover:bg-red-700",
      },
    },
    defaultVariants: { variant: "primary" },
  },
);

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  loading?: boolean;
}

export function Button({ className, variant, loading, children, disabled, ...props }: ButtonProps) {
  return (
    <button className={cn(buttonVariants({ variant }), className)} disabled={disabled || loading} {...props}>
      {loading && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div className={cn("rounded-xl border border-slate-200 bg-white p-5 shadow-sm", className)}>{children}</div>
  );
}

export function PageTitle({ title, caption }: { title: string; caption?: string }) {
  return (
    <div className="mb-5">
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      {caption && <p className="mt-1 text-sm text-ink-500">{caption}</p>}
    </div>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-ink-500">{hint}</span>}
    </label>
  );
}

const inputClass =
  "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-brand-600 focus:outline-none focus:ring-1 focus:ring-brand-600";

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cn(inputClass, props.className)} />;
}

export function Textarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={cn(inputClass, props.className)} />;
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={cn(inputClass, props.className)} />;
}

export function Alert({ tone, children }: { tone: "info" | "success" | "warning" | "error"; children: ReactNode }) {
  const tones: Record<string, string> = {
    info: "border-sky-200 bg-sky-50 text-sky-900",
    success: "border-emerald-200 bg-emerald-50 text-emerald-900",
    warning: "border-amber-200 bg-amber-50 text-amber-900",
    error: "border-red-200 bg-red-50 text-red-900",
  };
  return <div className={cn("rounded-lg border px-4 py-3 text-sm", tones[tone])}>{children}</div>;
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-ink-500" role="status">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
      {label ?? "Loading…"}
    </div>
  );
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "good" | "warn" | "bad" | "brand" }) {
  const tones: Record<string, string> = {
    neutral: "bg-slate-100 text-slate-700",
    good: "bg-emerald-100 text-emerald-800",
    warn: "bg-amber-100 text-amber-800",
    bad: "bg-red-100 text-red-800",
    brand: "bg-brand-100 text-brand-700",
  };
  return (
    <span className={cn("inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium", tones[tone])}>
      {children}
    </span>
  );
}

export function Progress({ value, label }: { value: number; label: string }) {
  return (
    <div>
      <div className="h-2 overflow-hidden rounded-full bg-slate-200" role="progressbar" aria-valuenow={Math.round(value * 100)}>
        <div className="h-full rounded-full bg-brand-600" style={{ width: `${Math.round(value * 100)}%` }} />
      </div>
      <p className="mt-1 text-xs text-ink-500">{label}</p>
    </div>
  );
}

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4" role="dialog" aria-modal="true">
      <div className="max-h-[85vh] w-full max-w-2xl overflow-auto rounded-xl bg-white p-6 shadow-lg">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">{title}</h2>
          <Button variant="ghost" onClick={onClose} aria-label="Close">
            ✕
          </Button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
}: {
  options: T[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div className="inline-flex rounded-lg border border-slate-300 bg-white p-1" role="tablist">
      {options.map((option) => (
        <button
          key={option}
          role="tab"
          aria-selected={option === value}
          onClick={() => onChange(option)}
          className={cn(
            "rounded-md px-3 py-1.5 text-sm font-medium",
            option === value ? "bg-brand-600 text-white" : "text-ink-500 hover:bg-slate-100",
          )}
        >
          {option}
        </button>
      ))}
    </div>
  );
}

export function Thinking({ message }: { message: string }) {
  return (
    <div className="my-3 flex items-center gap-4 rounded-xl border border-brand-600/30 bg-brand-50 p-5" role="status" aria-live="polite">
      <div className="thinking-bars" aria-hidden>
        <i />
        <i />
        <i />
        <i />
        <i />
      </div>
      <div>
        <strong>{message}</strong>
        <div className="mt-1 text-sm text-ink-500">Your progress is saved. Please keep this page open.</div>
      </div>
    </div>
  );
}
