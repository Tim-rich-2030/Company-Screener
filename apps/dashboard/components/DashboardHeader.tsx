// 모든 화면 상단 고정 헤더 (명세 §62). 데이터 출처는 v_data_cutoff 하나뿐이다.
import { getCutoff } from "@/lib/db";
import { kst, kstDate, statusDot } from "@/lib/format";

export default async function DashboardHeader() {
  const c = await getCutoff();
  const now = new Date();
  return (
    <header className="mb-8 border-b border-neutral-200 pb-4">
      <div className="flex items-center gap-2">
        <span className={`inline-block h-3 w-3 rounded-full ${statusDot[c.overall_status]}`} />
        <span className="text-sm font-bold">SYSTEM {c.overall_status}</span>
        <span className="ml-auto font-mono text-sm text-neutral-500">
          현재 {kst(now)}
        </span>
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-x-8 gap-y-1 text-sm sm:grid-cols-3 lg:grid-cols-5">
        <div>
          <dt className="text-xs text-neutral-500">Last Pipeline</dt>
          <dd className="font-mono">{kst(c.last_pipeline_at)}</dd>
        </div>
        <div>
          <dt className="text-xs text-neutral-500">Market Data Through</dt>
          <dd className="font-mono">{kst(c.market_complete_through)}</dd>
        </div>
        <div>
          <dt className="text-xs text-neutral-500">Policy Data Through</dt>
          <dd className="font-mono">{kst(c.policy_complete_through)}</dd>
        </div>
        <div>
          <dt className="text-xs text-neutral-500">Search Trend Data</dt>
          <dd className="font-mono">{kstDate(c.search_trend_data_through)}</dd>
        </div>
        <div>
          <dt className="text-xs text-neutral-500">Core Sources</dt>
          <dd className="font-mono">
            {c.sources_green} / {c.sources_total} HEALTHY
          </dd>
        </div>
      </dl>
    </header>
  );
}
