// eslint-config-next 16 原生导出 flat config 数组，无需 FlatCompat
import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypeScript from "eslint-config-next/typescript";

const config = [
  {
    ignores: [".next/**", "node_modules/**", "db/migrations/**", "next-env.d.ts"],
  },
  ...nextCoreWebVitals,
  ...nextTypeScript,
  {
    // 显式声明 React 版本：eslint-plugin-react 7.37 的自动探测在 ESLint 10 下会崩溃
    // （detectReactVersion 依赖已移除的 context.getFilename）
    settings: { react: { version: "19.2" } },
    rules: {
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
  {
    // components/ 下以客户端组件为主。服务端模块的真正防线是 `import "server-only"`
    // （客户端引入会构建失败），这里只是让错误更早、提示更清楚。
    files: ["components/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["@/lib/env", "@/db/*", "@/lib/agent-client"],
              message:
                "服务端模块不能在组件中引入。数据请由 Server Component 或 Route Handler 传入（DEVELOPMENT_PLAN.md §17.1）",
            },
          ],
        },
      ],
    },
  },
];

export default config;
