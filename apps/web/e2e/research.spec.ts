import { expect, test } from "@playwright/test";

/**
 * 打桩 Agent 后的完整 UI 路径（P6-11）。
 *
 * 共用一份 e2e SQLite，必须串行：Next 只有一个进程、一个库。
 */
test.describe.configure({ mode: "serial" });

const QUESTION = "E2E-HYPE 协议收入怎么样？";

test("提问后看到计划、报告、来源和成本", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByLabel("研究问题")).toBeVisible();
  await expect(page.getByLabel("模型")).toContainText("DeepSeek V4 Pro");

  await page.getByLabel("研究问题").fill(QUESTION);
  await page.getByRole("button", { name: "开始研究" }).click();

  await expect(page.getByRole("heading", { name: "HYPE 协议收入简报" })).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByText("已完成", { exact: true })).toBeVisible();
  await expect(page.getByRole("group", { name: "成本 $0.02 / $1.00" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Sources (1)" })).toBeVisible();
  await expect(page.getByText("制定计划", { exact: false })).toBeVisible();
  await expect(page.getByText("执行研究", { exact: false })).toBeVisible();
  await expect(page.getByText("撰写报告", { exact: false })).toBeVisible();

  await page.getByRole("button", { name: /研究过程/ }).click();
  await expect(page.getByText("Crypto Research", { exact: true })).toBeVisible();
  await expect(page.getByText("获取 HYPE 协议手续费收入")).toBeVisible();
  await expect(page.getByText("get_tvl")).toBeVisible();
});

test("历史列表能点进去回放同一份报告", async ({ page }) => {
  await page.goto("/history");

  const entry = page.getByRole("link", { name: QUESTION });
  await expect(entry).toBeVisible();
  await entry.click();

  await expect(page).toHaveURL(/[?&]session=/);
  await expect(page.getByRole("heading", { name: "HYPE 协议收入简报" })).toBeVisible();
  await expect(page.getByRole("group", { name: "成本 $0.02 / $1.00" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Sources (1)" })).toBeVisible();
});

test("设置页保存成功", async ({ page }) => {
  await page.goto("/settings");

  await page.getByLabel("语言").selectOption("en");
  await page.getByRole("button", { name: "保存" }).click();
  await expect(page.getByText("已保存，下次研究会话生效。")).toBeVisible();
});

test("调试页能回答阶段耗时并列出 Provider", async ({ page }) => {
  await page.goto("/debug");

  await expect(page.getByRole("heading", { name: "为什么这次研究花了这么久？" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "哪个 Agent 最慢？" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "哪个 Tool 最容易失败？" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "哪个模型效果最好？" })).toBeVisible();
  await expect(page.getByText(QUESTION)).toBeVisible();
  await expect(page.getByRole("region", { name: "Provider 缓存与配额" })).toContainText("tavily");
});

test("慢流可以点取消", async ({ page }) => {
  await page.goto("/");

  await page.getByLabel("研究问题").fill("[e2e-slow] 请慢慢研究");
  await page.getByRole("button", { name: "开始研究" }).click();
  await page.getByRole("button", { name: "取消研究" }).click();
  await expect(page.getByText("已取消", { exact: true })).toBeVisible();
});
