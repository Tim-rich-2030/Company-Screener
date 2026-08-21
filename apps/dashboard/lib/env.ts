// 환경 구분 (M1.5 §11). 명시되지 않으면 MOCK으로 간주한다 —
// mock 데이터를 실데이터로 오인하는 방향의 실수를 구조적으로 차단하는 안전 기본값.
export type RadarEnv = "PRODUCTION" | "STAGING" | "MOCK";

export function radarEnv(): RadarEnv {
  const v = (process.env.NEXT_PUBLIC_RADAR_ENV ?? "").toUpperCase();
  if (v === "PRODUCTION" || v === "STAGING") return v;
  return "MOCK";
}

export function isRealEnv(): boolean {
  return radarEnv() !== "MOCK";
}

export function gitSha(): string {
  return process.env.NEXT_PUBLIC_GIT_SHA ?? "unknown";
}

export function buildTime(): string {
  return process.env.NEXT_PUBLIC_BUILD_TIME ?? "unknown";
}

export const envBadgeStyle: Record<RadarEnv, string> = {
  PRODUCTION: "bg-emerald-700 text-white",
  STAGING: "bg-sky-700 text-white",
  MOCK: "bg-amber-500 text-white",
};
