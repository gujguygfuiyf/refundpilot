import type { ReactNode } from "react";
import { label } from "@/lib/format";

type Tone = "gray" | "blue" | "green" | "amber" | "red" | "violet";

const TONES: Record<Tone, string> = {
  gray: "bg-gray-100 text-gray-700 ring-gray-200",
  blue: "bg-blue-50 text-blue-700 ring-blue-200",
  green: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  amber: "bg-amber-50 text-amber-800 ring-amber-200",
  red: "bg-red-50 text-red-700 ring-red-200",
  violet: "bg-violet-50 text-violet-700 ring-violet-200",
};

export function Badge({ tone = "gray", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${TONES[tone]}`}>
      {children}
    </span>
  );
}

const STATUS_TONE: Record<string, Tone> = {
  SUBMITTED: "gray",
  ANALYZING: "violet",
  ANALYZED: "violet",
  ANALYZE_FAILED: "red",
  REVIEW_REQUIRED: "blue",
  AUTO_ELIGIBLE: "blue",
  EVIDENCE_REQUESTED: "amber",
  APPROVED: "blue",
  PROCESSING: "violet",
  COMPLETED: "green",
  FAILED: "red",
  REJECTED: "red",
  CANCELLED: "gray",
  UNKNOWN: "amber",
  PENDING: "amber",
  CREATED: "gray",
};

export function StatusBadge({ status }: { status: string }) {
  return <Badge tone={STATUS_TONE[status] ?? "gray"}>{label(status)}</Badge>;
}

const OUTCOME_TONE: Record<string, Tone> = {
  FULL_REFUND: "green",
  PARTIAL_REFUND: "blue",
  REQUEST_EVIDENCE: "amber",
  REVIEW: "violet",
  REJECT: "red",
};

export function OutcomeBadge({ outcome }: { outcome: string | null | undefined }) {
  if (!outcome) return <span className="text-gray-400">—</span>;
  return <Badge tone={OUTCOME_TONE[outcome] ?? "gray"}>{label(outcome)}</Badge>;
}

const GOOD = new Set(["confirmed", "likely", "sufficient", "yes"]);
const BAD = new Set(["mismatch", "unlikely", "insufficient", "no", "shattered", "broken", "wrong_item"]);

export function FactChip({ value }: { value: string | boolean | null | undefined }) {
  if (value === null || value === undefined) return <span className="text-gray-400">—</span>;
  const v = String(value);
  const tone: Tone = GOOD.has(v) ? "green" : BAD.has(v) ? "red" : v === "true" ? "amber" : "gray";
  return <Badge tone={tone}>{label(v)}</Badge>;
}

export function Card({ title, aside, children, className = "" }: { title?: ReactNode; aside?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-xl border border-gray-200 bg-white p-5 shadow-sm ${className}`}>
      {(title || aside) && (
        <div className="mb-4 flex items-center justify-between gap-3">
          {title && <h2 className="text-sm font-semibold tracking-wide text-gray-900 uppercase">{title}</h2>}
          {aside}
        </div>
      )}
      {children}
    </section>
  );
}

export function Button({
  variant = "primary",
  className = "",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" | "ghost" }) {
  const styles = {
    primary: "bg-brand text-white hover:bg-brand-dark",
    secondary: "bg-white text-gray-800 ring-1 ring-gray-300 hover:bg-gray-50",
    danger: "bg-white text-red-700 ring-1 ring-red-300 hover:bg-red-50",
    ghost: "text-gray-600 hover:bg-gray-100",
  }[variant];
  return (
    <button
      {...props}
      className={`inline-flex items-center justify-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${styles} ${className}`}
    />
  );
}

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null;
  return <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 ring-1 ring-red-200">{error}</p>;
}

export function Spinner() {
  return <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />;
}
