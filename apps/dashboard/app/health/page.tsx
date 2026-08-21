// 화면: SOURCES / HEALTH — V1 핵심 화면 (명세 §42).
// "Action이 실제로 돌았는가"를 GitHub을 열지 않고 확인하는 곳.
import DashboardHeader from "@/components/DashboardHeader";
import { sql } from "@/lib/db";
import { buildTime, gitSha, isRealEnv, radarEnv } from "@/lib/env";
import { ageText, kst, statusColor } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function HealthPage() {
  // Database Connected — 실패해도 화면은 뜨고 상태로 보여준다 (fail-closed 표시)
  type InfraRow = {
    last_live_workflow_name: string | null;
    last_live_workflow_at: Date | null;
    last_live_github_run_id: string | null;
    live_data_connected: boolean;
  };
  let dbConnected = false;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let rows: any[] = [];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let alerts: any[] = [];
  let infra: InfraRow | null = null;
  try {
    rows = await sql()`select * from v_system_health order by source_type, name`;
    alerts = await sql()`
      select severity, component, message, created_at
      from alerts where resolved_at is null order by created_at desc`;
    const inf = await sql()`select * from v_infra_status`;
    infra = inf[0] as unknown as InfraRow;
    dbConnected = true;
  } catch {
    dbConnected = false;
  }

  const realEnv = isRealEnv();

  return (
    <div>
      <DashboardHeader />
      <h1 className="text-lg font-bold">SOURCES / HEALTH</h1>

      {/* 인프라 정보 블록 (M1.5 §12) */}
      <dl className="mt-4 grid grid-cols-2 gap-x-8 gap-y-1 rounded border border-neutral-200 bg-white p-3 text-sm sm:grid-cols-3">
        <div>
          <dt className="text-xs text-neutral-500">Environment</dt>
          <dd className="font-mono font-bold">{radarEnv()}</dd>
        </div>
        <div>
          <dt className="text-xs text-neutral-500">Git Commit SHA</dt>
          <dd className="font-mono">{gitSha()}</dd>
        </div>
        <div>
          <dt className="text-xs text-neutral-500">App Build Time</dt>
          <dd className="font-mono">{kst(buildTime())}</dd>
        </div>
        <div>
          <dt className="text-xs text-neutral-500">Database Connected</dt>
          <dd className={`font-mono font-bold ${dbConnected ? "text-emerald-700" : "text-red-700"}`}>
            {dbConnected ? "YES" : "NO"}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-neutral-500">Last Successful GitHub Workflow</dt>
          <dd className="font-mono">
            {infra?.last_live_workflow_name
              ? `${infra.last_live_workflow_name} · ${kst(infra.last_live_workflow_at)}` +
                (infra.last_live_github_run_id ? ` · run ${infra.last_live_github_run_id}` : "")
              : "기록 없음"}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-neutral-500">Live Data Sources</dt>
          <dd className={`font-mono font-bold ${infra?.live_data_connected ? "text-emerald-700" : "text-neutral-500"}`}>
            {infra?.live_data_connected ? "CONNECTED" : "NOT CONNECTED"}
          </dd>
        </div>
      </dl>

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
                  {realEnv && !r.last_live_success_at ? (
                    // 실환경에서 live 수집 성공이 없는 소스는 GREEN/RED가 아니라
                    // NOT CONNECTED다 (M1.5 §12 — mock run은 연결로 치지 않는다)
                    <span className="rounded border border-neutral-300 bg-neutral-100 px-2 py-0.5 text-xs font-bold text-neutral-500">
                      NOT CONNECTED
                    </span>
                  ) : (
                    <span className={`rounded border px-2 py-0.5 text-xs font-bold ${statusColor[r.status]}`}>
                      {r.status}
                    </span>
                  )}
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
