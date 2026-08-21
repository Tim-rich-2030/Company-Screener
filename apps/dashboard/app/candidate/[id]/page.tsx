// 화면: Candidate Detail (명세 §39) — score breakdown, metrics, evidence drill-down.
import Link from "next/link";
import { notFound } from "next/navigation";
import DashboardHeader from "@/components/DashboardHeader";
import { sql } from "@/lib/db";
import { kst, kstDate, statusColor } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function CandidatePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const cand = await sql()`select * from v_today where candidate_id = ${id}`;
  if (cand.length === 0) notFound();
  const c = cand[0];
  const comp = c.components as {
    early_signal: Record<string, number>;
    opportunity: Record<string, number>;
    confidence: Record<string, number>;
    metrics: Record<string, unknown>;
    source_status: Record<string, string>;
    reasons?: string[];
    risks?: string[];
  };
  const evidence = await sql()`
    select * from v_candidate_evidence where candidate_id = ${id}
    order by published_at desc nulls last, fetched_at desc`;
  const metrics = await sql()`
    select * from candidate_metrics where candidate_id = ${id}
    order by window_end desc limit 5`;

  const bySourceType = evidence.reduce<Record<string, number>>((acc, e) => {
    acc[e.source_type] = (acc[e.source_type] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div>
      <DashboardHeader />
      <p className="text-xs"><Link href="/today" className="text-neutral-500 underline">← TODAY</Link></p>
      <div className="mt-1 flex flex-wrap items-baseline gap-3">
        <h1 className="text-xl font-bold">{c.cluster_name}</h1>
        <span className="rounded bg-neutral-100 px-2 py-0.5 text-xs">{c.category}</span>
        <span className="rounded bg-neutral-100 px-2 py-0.5 text-xs">{c.candidate_type}</span>
        <span className={`rounded px-2 py-0.5 text-xs font-bold ${
          c.lifecycle === "now" ? "bg-emerald-100 text-emerald-800" : "bg-neutral-200"}`}>
          {String(c.lifecycle).toUpperCase()}
        </span>
        <span className={`rounded border px-2 py-0.5 text-xs font-bold ${
          c.freshness_pass ? statusColor.GREEN : statusColor.RED}`}>
          FRESHNESS {c.freshness_pass ? "PASS" : "FAIL"}
        </span>
      </div>
      <p className="mt-1 font-mono text-sm text-neutral-500">
        Opportunity {Number(c.opportunity).toFixed(1)} · Confidence {Number(c.confidence).toFixed(1)} ·
        Rank {Number(c.rank_score).toFixed(1)} · 데이터 기준 {kst(c.data_complete_through)} ·
        계산 {kst(c.calculated_at)} · {c.score_version}
      </p>

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <section>
          <h2 className="text-sm font-bold text-neutral-500">Score Breakdown</h2>
          {(["early_signal", "opportunity", "confidence"] as const).map((group) => (
            <div key={group} className="mt-2">
              <p className="font-mono text-xs text-neutral-400">{group}</p>
              <table className="mt-1 w-full text-sm">
                <tbody>
                  {Object.entries(comp[group]).map(([k, v]) => (
                    <tr key={k} className="border-b border-neutral-100">
                      <td className="py-1 pr-2 font-mono text-xs">
                        {k}
                        {group === "opportunity" && (k === "blog_fit" || k === "monetization") && (
                          // M1.5 §15-B: 실제 logic 구현 전까지 stub 성분임을 명시
                          <span className="ml-1 rounded bg-amber-100 px-1 py-0.5 text-[10px] font-bold text-amber-800">
                            PROVISIONAL
                          </span>
                        )}
                      </td>
                      <td className="w-16 py-1 text-right font-mono">{Number(v).toFixed(1)}</td>
                      <td className="w-1/2 py-1 pl-3">
                        <div className="h-1.5 rounded bg-neutral-100">
                          <div className="h-1.5 rounded bg-neutral-700"
                               style={{ width: `${Math.min(100, Number(v))}%` }} />
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        </section>

        <section>
          <h2 className="text-sm font-bold text-neutral-500">Metrics (최근 윈도우)</h2>
          <table className="mt-2 w-full text-sm">
            <thead>
              <tr className="border-b border-neutral-300 text-left text-xs text-neutral-500">
                <th className="py-1 pr-2">Window End</th>
                <th className="py-1 pr-2">6h 문서</th>
                <th className="py-1 pr-2">Velocity</th>
                <th className="py-1 pr-2">Accel</th>
                <th className="py-1 pr-2">Novelty</th>
                <th className="py-1">Sources</th>
              </tr>
            </thead>
            <tbody>
              {metrics.map((m, i) => (
                <tr key={i} className="border-b border-neutral-100 font-mono text-xs">
                  <td className="py-1 pr-2">{kst(m.window_end)}</td>
                  <td className="py-1 pr-2">{m.distinct_documents}</td>
                  <td className="py-1 pr-2">{Number(m.velocity).toFixed(2)}</td>
                  <td className="py-1 pr-2">{Number(m.acceleration).toFixed(2)}</td>
                  <td className="py-1 pr-2">{Number(m.novelty).toFixed(0)}</td>
                  <td className="py-1">{m.distinct_sources}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <h2 className="mt-6 text-sm font-bold text-neutral-500">Source Breakdown</h2>
          <div className="mt-2 flex flex-wrap gap-2 text-xs">
            {Object.entries(bySourceType).map(([t, n]) => (
              <span key={t} className="rounded border border-neutral-200 px-2 py-1 font-mono">
                {t}: {n}
              </span>
            ))}
          </div>

          <h2 className="mt-6 text-sm font-bold text-neutral-500">Source Freshness</h2>
          <div className="mt-2 flex flex-wrap gap-2 text-xs">
            {Object.entries(comp.source_status).map(([name, st]) => (
              <span key={name} className={`rounded border px-2 py-1 font-mono ${statusColor[st]}`}>
                {name}: {st}
              </span>
            ))}
          </div>

          {comp.risks && comp.risks.length > 0 && (
            <>
              <h2 className="mt-6 text-sm font-bold text-neutral-500">
                Risk — 왜 실패할 수 있는가
              </h2>
              <ul className="mt-1 space-y-0.5 text-sm">
                {comp.risks.map((r, i) => (<li key={i}>− {r}</li>))}
              </ul>
            </>
          )}
        </section>
      </div>

      <section className="mt-8">
        <h2 className="text-sm font-bold text-neutral-500">
          Evidence — {evidence.length} documents
        </h2>
        <table className="mt-2 w-full text-sm">
          <thead>
            <tr className="border-b border-neutral-300 text-left text-xs text-neutral-500">
              <th className="py-1 pr-3">Title</th>
              <th className="py-1 pr-3">Source</th>
              <th className="py-1 pr-3">Published At</th>
              <th className="py-1 pr-3">Precision</th>
              <th className="py-1 pr-3">Fetched At</th>
              <th className="py-1">Source Status</th>
            </tr>
          </thead>
          <tbody>
            {evidence.map((e, i) => (
              <tr key={i} className="border-b border-neutral-100">
                <td className="max-w-[24rem] truncate py-1.5 pr-3">
                  {e.canonical_url ? (
                    <a href={e.canonical_url} target="_blank" rel="noreferrer" className="hover:underline">
                      {e.title} ↗
                    </a>
                  ) : e.title}
                </td>
                <td className="py-1.5 pr-3 font-mono text-xs">{e.source_name}</td>
                <td className="py-1.5 pr-3 font-mono text-xs">
                  {e.published_precision === "UNKNOWN" ? "미제공"
                    : e.published_precision === "DAY" ? kstDate(e.published_at)
                    : kst(e.published_at)}
                </td>
                <td className="py-1.5 pr-3 font-mono text-xs">{e.published_precision}</td>
                <td className="py-1.5 pr-3 font-mono text-xs">{kst(e.fetched_at)}</td>
                <td className="py-1.5">
                  <span className={`rounded border px-1.5 py-0.5 text-xs ${statusColor[e.source_status]}`}>
                    {e.source_status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
