import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 대시보드는 항상 최신 DB 상태를 보여야 한다 (freshness가 제품이다)
  experimental: {},
};

export default nextConfig;
