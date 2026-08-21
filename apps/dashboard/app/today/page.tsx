// 화면: TODAY (명세 §37~§38). Fail-Closed 규칙은 docs/DATA_FRESHNESS.md §7.
import Link from "next/link";
import DashboardHeader from "@/components/DashboardHeader";
import { sql } from "@/lib/db";
import { kst } from "@/lib/format";

export const dynamic = "force-dynamic";

type TodayRow = {
  candidate_id: string;
  cluster_name: string;
  candidate_type: string;
  lifecycle: string;
  category: string;
  opportunity: string;
  confidence: string;
  rank_score: string;
  freshness_pass: boolean;
  calculated_at: Date;
  data_complete_through: Date | null;
  components: {
    reasons?: string[];
    risks?: string[];
  };
};

export default async function TodayPage() {
  const rows = (await sql()`select * from v_today`) as unknown as TodayRow[];
  const worstRed = await sql()`
    select name, last_success_at,
           extract(epoch from (now() - last_success_at))/60 as age_min
    from v_system_health where status = 'RED'
    order by last_success_at asc nulls first limit 1`;

  const latestCalc = rows[0]?.calculated_at ? new Date(rows[0].calculated_at) : null;
  const pipelineStale =
    !latestCalc || Date.now() - latestCalc.getTime() > 2 * 3600 * 1000;
  const anyGateFail = rows.some((r) => !r.freshness_pass);
  const suspended = worstRed.length > 0 || pipelineStale || anyGateFail;

  let suspendMessage: string | null = null;
  if (worstRed.length > 0) {
    const w = worstRed[0];
    const mins = w.age_min == null ? null : Math.floor(Number(w.age_min));
    suspendMessage =
      mins == null
        ? `추천 일시 중지 — ${w.name} 수집 성공 기록 없음`
        : `추천 일시 중지 — ${w.name} 데이터가 ${Math.floor(mins / 60)}시간 ${mins % 60}분 동안 갱신되지 않음 (마지막 성공: ${kst(w.last_success_at)})`;
  } else if (pipelineStale) {
    suspendMessage = "추천 일시 중지 — 파이프라인이 2시간 이상 실행되지 않음";
  } else if (anyGateFail) {
    suspendMessage = "추천 일시 중지 — Freshness Gate FAIL (필수 소스 상태 저하)";
  }

  const nowRows = rows.filter((r) => r.lifecycle === "now" && r.freshness_pass);
  const watchRows = rows.filter((r) => r.lifecycle === "watch");

  return (
    <div>
      <DashboardHeader />
      <h1 className="text-lg font-bold">TODAY</h1>

      {suspended && (
        <div className="mt-4 rounded border border-red-300 bg-red-50 p-4 text-sm font-medium text-red-900">
          {suspendMessage}
          <span className="ml-2 text-xs font-normal">
            (<Link href="/health" className="underline">SOURCES / HEALTH에서 상세 확인</Link>)
          </span>
        </div>
      )}

      {!suspended && nowRows.length > 0 && (
        <section className="mt-6">
          <h2 className="text-sm font-bold text-neutral-500">NOW — 지금 쓸 가치가 검증된 후보</h2>
          <div className="mt-2 space-y-4">
            {nowRows.map((r, i) => (
              <Card key={r.candidate_id} r={r} rank={i + 1} highlight />
            ))}
          </div>
        </section>
      )}

      <section className="mt-8">
        <h2 className="text-sm font-bold text-neutral-500">WATCH</h2>
        <div className="mt-2 space-y-4">
          {watchRows.map((r, i) => (
            <Card key={r.candidate_id} r={r} rank={nowRows.length + i + 1} />
          ))}
          {watchRows.length === 0 && (
            <p className="text-sm text-neutral-500">해당 없음</p>
          )}
        </div>
      </section>
    </div>
  );
}

function Card({ r, rank, highlight = false }: { r: TodayRow; rank: number; highlight?: boolean }) {
  return (
    <div className={`rounded border p-4 ${highlight ? "border-neutral-900 bg-white" : "border-neutral-200 bg-white"}`}>
      <div className="flex flex-wrap items-baseline gap-3">
        <span className="font-mono text-sm text-neutral-400">#{rank}</span>
        <Link href={`/candidate/${r.candidate_id}`} className="text-base font-bold hover:underline">
          {r.cluster_name}
        </Link>
        <span className="rounded bg-neutral-100 px-2 py-0.5 text-xs">{r.category}</span>
        <span className={`rounded px-2 py-0.5 text-xs font-bold ${
          r.lifecycle === "now" ? "bg-emerald-100 text-emerald-800" : "bg-neutral-100 text-neutral-600"
        }`}>
          {r.lifecycle.toUpperCase()}
        </span>
        <span className="ml-auto font-mono text-sm">
          Opportunity <b>{Number(r.opportunity).toFixed(0)}</b> · Confidence{" "}
          <b>{Number(r.confidence).toFixed(0)}</b>
        </span>
      </div>

      {r.components.reasons && r.components.reasons.length > 0 && (
        <div className="mt-3">
          <p className="text-xs font-bold text-neutral-500">왜 지금인가</p>
          <ul className="mt-1 space-y-0.5 text-sm">
            {r.components.reasons.slice(0, 5).map((reason, i) => (
              <li key={i}>+ {reason}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-3 flex flex-wrap gap-4 border-t border-neutral-100 pt-2 font-mono text-xs text-neutral-500">
        <span>데이터 기준 {kst(r.data_complete_through)}</span>
        <span>계산 {kst(r.calculated_at)}</span>
        <Link href={`/candidate/${r.candidate_id}`} className="ml-auto text-neutral-900 underline">
          근거 보기 →
        </Link>
      </div>
    </div>
  );
}
