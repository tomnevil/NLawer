const nextConfig = {
  // standalone：产物自带 server.js + 追踪到的最小依赖，供 Docker 直跑
  output: "standalone",
  reactStrictMode: true,
  transpilePackages: ["@nlaw/ui", "@nlaw/sdk", "@nlaw/types"],
};

module.exports = nextConfig;
