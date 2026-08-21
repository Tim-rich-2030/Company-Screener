// 화면: SOURCES / HEALTH — V1 핵심 화면 (명세 §42).
// "Action이 실제로 돌았는가"를 GitHub을 열지 않고 확인하는 곳.
import DashboardHeader from "@/components/DashboardHeader";
import { sql } from "@/lib/db";
import { ageText, kst, statusColor } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function HealthPage() {
  const rows = await sql()`select * from v_system_health order by source_type, name`;
  const alerts = await sql()`
    select severity, component, message, created_at
    from alerts where resolved_at is null order by created_at desc`;

  return (
    <div>
      <DashboardHeader />
      <h1 className="text-lg font-bold">SOURCES / HEALTH</h1>

      {alerts.length > 0 && (
        <div className="mt-4 rounded border border-red-300 bg-red-50 p-3 text-sm text-red-900">
          <p className="font-bold">미해소 알림 {alerts.length}건</p>
          <ul className="mt-1 list-disc pl-5">
            {alerts.map((a, i) => (
              <li key={i}>
                <span className="font-mono">{a.component}</span> — {a.message}{" "}
                <span className="text-red-600">({kst(a.created_at)})</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-4 overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-neutral-300 text-left text-xs text-neutral-500">
              <th className="py-2 pr-3">Source</th>
              <th className="py-2 pr-3">Status</th>
              <th className="py-2 pr-3">Last Run</th>
              <th className="py-2 pr-3">Last Success</th>
              <th className="py-2 pr-3">Data Through</th>
              <th className="py-2 pr-3">Precision</th>
              <th className="py-2 pr-3">Rows</th>
              <th className="py-2 pr-3">New</th>
              <th className="py-2">Error</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.name} className="border-b border-neutral-200">
                <td className="py-2 pr-3 font-mono">{r.name}</td>
                <td className="py-2 pr-3">
                  <span className={`rounded border px-2 py-0.5 text-xs font-bold ${statusColor[r.status]}`}>
                    {r.status}
                  </span>
                </td>
                <td className="py-2 pr-3">
                  {kst(r.last_run_at)}
                  {r.last_run_status === "failed" && (
                    <span className="ml-1 text-xs text-red-700">(failed)</span>
                  )}
                </td>
                <td className="py-2 pr-3">
                  {kst(r.last_success_at)}
                  <span className="ml-1 text-xs text-neutral-400">{ageText(r.last_success_at)}</span>
                </td>
                <td className="py-2 pr-3 font-mono">{kst(r.data_through)}</td>
                <td className="py-2 pr-3 font-mono text-xs">{r.published_precision}</td>
                <td className="py-2 pr-3 font-mono">{r.rows_received ?? "—"}</td>
                <td className="py-2 pr-3 font-mono">{r.rows_new ?? "—"}</td>
                <td className="max-w-[16rem] truncate py-2 text-xs text-red-700" title={r.last_error ?? ""}>
                  {r.last_error ?? ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-xs text-neutral-500">
        Data Through = 이 시각까지의 데이터를 보유했다고 보증하는 시각
        (fetch 시각이 아님 — DAY 단위 소스는 데이터 기준일). Precision = 원문 게시시각 해상도.
        DAY/UNKNOWN 소스는 6h velocity의 근거로 쓰이지 않습니다.
      </p>
    </div>
  );
}
