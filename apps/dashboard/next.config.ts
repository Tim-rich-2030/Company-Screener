import type { NextConfig } from "next";
import { execSync } from "node:child_process";

// 빌드 시점 메타데이터 (M1.5 §12: Git SHA / Build Time 화면 표시).
// Vercel에서는 VERCEL_GIT_COMMIT_SHA가 주입되고, 로컬은 git에서 읽는다.
function resolveGitSha(): string {
  if (process.env.VERCEL_GIT_COMMIT_SHA) return process.env.VERCEL_GIT_COMMIT_SHA.slice(0, 7);
  try {
    return execSync("git rev-parse --short HEAD", { stdio: ["ignore", "pipe", "ignore"] })
      .toString().trim();
  } catch {
    return "unknown";
  }
}

const nextConfig: NextConfig = {
  env: {
    NEXT_PUBLIC_GIT_SHA: resolveGitSha(),
    NEXT_PUBLIC_BUILD_TIME: new Date().toISOString(),
  },
};

export default nextConfig;
