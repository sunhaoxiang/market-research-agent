import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // better-sqlite3 是原生模块，必须由 Node 运行时加载而非打包
  serverExternalPackages: ["better-sqlite3"],
  typedRoutes: true,
};

export default nextConfig;
