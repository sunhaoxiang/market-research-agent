import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    include: ["**/*.test.{ts,tsx}"],
    exclude: ["node_modules/**", ".next/**", "e2e/**"],
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL(".", import.meta.url)),
      "@mra/shared": fileURLToPath(new URL("../../packages/shared/src/index.ts", import.meta.url)),
      // `server-only` 只在 `react-server` 导出条件下解析到空实现，而那个条件
      // 只有 Next 的构建会设；vitest 拿到的是会主动 throw 的那份。指到空模块
      // 而不是给 vitest 加上 react-server 条件——后者会让 react 也解析到
      // server 版本（没有 useState），把组件测试全部搞挂
      "server-only": fileURLToPath(new URL("./test/server-only-stub.ts", import.meta.url)),
    },
  },
});
