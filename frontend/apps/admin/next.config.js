const nextConfig = {
  // standalone：产物自带 server.js + 追踪到的最小依赖，供 Docker 直跑
  output: "standalone",
  reactStrictMode: true,
  transpilePackages: ["@nlaw/ui", "@nlaw/sdk", "@nlaw/types"],
  // 挂在 https://bot.tomneil.asia/admin 下（nginx 按路径转发，应用需感知前缀）
  basePath: "/admin",
};

module.exports = nextConfig;
