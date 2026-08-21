// 표시 전용 KST 포맷터. 저장·계산은 항상 UTC (docs/DATA_FRESHNESS.md §1).
const KST = "Asia/Seoul";

export function kst(dt: Date | string | null | undefined, withDate = true): string {
  if (!dt) return "—";
  const d = typeof dt === "string" ? new Date(dt) : dt;
  const opts: Intl.DateTimeFormatOptions = withDate
    ? { timeZone: KST, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }
    : { timeZone: KST, hour: "2-digit", minute: "2-digit", hour12: false };
  return new Intl.DateTimeFormat("ko-KR", opts).format(d) + " KST";
}

export function kstDate(dt: Date | string | null | undefined): string {
  if (!dt) return "—";
  const d = typeof dt === "string" ? new Date(dt) : dt;
  return new Intl.DateTimeFormat("ko-KR", { timeZone: KST, year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
}

export function ageText(dt: Date | string | null | undefined, now = new Date()): string {
  if (!dt) return "기록 없음";
  const d = typeof dt === "string" ? new Date(dt) : dt;
  const mins = Math.max(0, Math.floor((now.getTime() - d.getTime()) / 60000));
  if (mins < 60) return `${mins}분 전`;
  return `${Math.floor(mins / 60)}시간 ${mins % 60}분 전`;
}

export const statusColor: Record<string, string> = {
  GREEN: "bg-emerald-100 text-emerald-800 border-emerald-300",
  YELLOW: "bg-amber-100 text-amber-800 border-amber-300",
  RED: "bg-red-100 text-red-800 border-red-300",
};

export const statusDot: Record<string, string> = {
  GREEN: "bg-emerald-500",
  YELLOW: "bg-amber-500",
  RED: "bg-red-500",
};
