// 서버 전용 DB 접근. DATABASE_URL은 절대 클라이언트로 내려가지 않는다.
// 로컬/mock: 로컬 Postgres. 운영: Supabase 연결 문자열(서버 컴포넌트에서만).
import "server-only";
import postgres from "postgres";

declare global {
  // eslint-disable-next-line no-var
  var __sql: ReturnType<typeof postgres> | undefined;
}

export function sql() {
  if (!global.__sql) {
    const url = process.env.DATABASE_URL;
    if (!url) throw new Error("DATABASE_URL is not set");
    global.__sql = postgres(url, { max: 4, prepare: false });
  }
  return global.__sql;
}

export type Cutoff = {
  market_complete_through: Date | null;
  policy_complete_through: Date | null;
  search_trend_data_through: Date | null;
  last_pipeline_at: Date | null;
  sources_green: number;
  sources_total: number;
  overall_status: "GREEN" | "YELLOW" | "RED";
};

export async function getCutoff(): Promise<Cutoff> {
  const rows = await sql()`select * from v_data_cutoff`;
  return rows[0] as unknown as Cutoff;
}
