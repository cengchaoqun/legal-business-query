// 浏览器回归使用独立API桩验证前端分支；真实数据库联调另行执行。
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
  });
  const page = await browser.newPage({
    viewport: { width: 1280, height: 850 },
  });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  let logged = false,
    role = "analyst",
    queries = [],
    submitted = 0;
  const metadata = {
    version: "test-v1",
    synthetic: true,
    model_mode: "mock",
    data_watermark: "2026-10-20",
    schema: {
      version: "test-v1",
      tables: {
        v_case: {
          grain: "每案件一行",
          columns: { case_id: "INTEGER 主键", status: "VARCHAR 当前状态" },
        },
      },
      relationships: ["v_case.owner_team_id -> v_team.team_id"],
    },
    dictionary: { "v_case.status": { OPEN: ["在办"] } },
    metrics: [
      {
        id: "net_receipts",
        name: "期间净回款",
        rule: "已确认收款减退款，按入账时间统计",
      },
    ],
  };
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url()),
      p = url.pathname,
      method = request.method();
    let data = {},
      status = 200;
    if (p === "/api/me") {
      if (!logged) status = 401;
      else
        data = {
          id: "u1",
          username: "测试用户",
          role,
          organization_id: "测试机构",
          scope_id: "授权演示范围",
        };
    } else if (p === "/api/auth/login") logged = true;
    else if (p === "/api/auth/logout") logged = false;
    else if (p === "/api/metadata") data = metadata;
    else if (p === "/api/conversations" && method === "POST")
      data = { conversation_id: "c-test" };
    else if (p === "/api/conversations")
      data = {
        items: queries.length
          ? [
              {
                id: "c-test",
                title: "测试问数会话",
                updated_at: "2026-10-22",
                status: queries.at(-1).status,
              },
            ]
          : [],
      };
    else if (p === "/api/conversations/c-test/queries" && method === "POST") {
      submitted++;
      const question = request.postDataJSON().question;
      const q = {
        query_id: "q-" + submitted,
        conversation_id: "c-test",
        question,
        status: question.includes("结案率")
          ? "clarify"
          : question.includes("删除")
            ? "refuse"
            : question.includes("失败")
              ? "failed"
              : "succeeded",
        confirmed_conditions: { year: 2025 },
      };
      if (q.status === "clarify")
        q.decision = {
          question: "请确认期间收结比，还是新收案件截至日结案率？",
          reason: "两种统计分母不同",
        };
      if (q.status === "refuse") q.decision = { reason: "仅允许只读经营查询" };
      if (q.status === "failed") {
        q.error_code = "MODEL_TIMEOUT";
        q.error_message = "模型服务暂不可用";
      }
      if (q.status === "succeeded")
        q.result = {
          summary: "接口测试返回的团队净回款",
          table: {
            columns: ["团队", "净回款"],
            rows: [
              { 团队: "测试甲", 净回款: "1200.50" },
              { 团队: "测试乙", 净回款: null },
            ],
          },
          chart: {
            kind: "bar",
            x: ["测试甲", "测试乙"],
            series: [{ name: "净回款", data: ["1200.50", null] }],
          },
          basis: {
            metrics: metadata.metrics,
            conditions: { time_field: "received_at", year: 2025 },
            timezone: "Asia/Shanghai",
            data_watermark: "2026-10-20",
            scope: "授权演示范围",
            metadata_version: "test-v1",
            model_service_version: "mock-test",
          },
          sql: "SELECT team_name, SUM(amount) FROM v_receipt GROUP BY team_name",
          warnings: ["仅为前端回归测试数据"],
        };
      queries.push(q);
      data = q;
      if (q.status === "failed") status = 503;
    } else if (p === "/api/conversations/c-test/queries")
      data = { conversation: { id: "c-test" }, items: queries };
    else if (p.endsWith("/feedback")) data = { feedback_id: "f1" };
    else if (p === "/api/admin/query-runs") data = { items: queries };
    else if (p === "/api/admin/system/status")
      data = {
        model: { mode: "mock", name: "legal-sql-v1", configured: false },
        database: { dialect: "sqlite", read_only: true },
        limits: { query_timeout_seconds: 10, max_rows: 1000 },
        metadata_version: "test-v1",
        data_watermark: "2026-10-20",
        synthetic: true,
      };
    else if (p === "/api/admin/model/test")
      data = { ok: true, mode: "mock", message: "模拟契约校验通过" };
    else if (p === "/api/admin/metadata/import") {
      assert.ok(request.postDataJSON().schema);
      data = { version: "test-v1" };
    } else {
      status = 404;
      data = { detail: "测试接口不存在" };
    }
    await route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(data),
    });
  });
  await page.goto("http://127.0.0.1:5173");
  await page.waitForLoadState("networkidle");
  assert.equal(
    await page.getByRole("heading", { name: "登录法律问数" }).count(),
    1,
  );
  await page.getByLabel("账号", { exact: true }).fill("analyst");
  await page.getByLabel("密码", { exact: true }).fill("test");
  await page.getByRole("button", { name: "登 录", exact: true }).click();
  await page
    .getByRole("heading", { name: "用一句话，了解案件经营情况" })
    .waitFor();
  assert.equal(await page.getByRole("button", { name: "系统配置" }).count(), 0);
  fs.mkdirSync(path.join(__dirname, "test-results"), { recursive: true });
  await page.screenshot({
    path: path.join(__dirname, "test-results/home-desktop.png"),
    fullPage: true,
  });
  await page.getByLabel("问数问题或澄清回复").fill("2025年结案率是多少？");
  await page.getByRole("button", { name: "开始问数" }).click();
  await page.getByRole("heading", { name: "需要确认统计条件" }).waitFor();
  await page.getByLabel("问数问题或澄清回复").fill("期间收结比");
  await page.getByRole("button", { name: "继续问数" }).click();
  await page.getByText("接口测试返回的团队净回款", { exact: true }).waitFor();
  assert.equal(
    await page.getByRole("cell", { name: "—", exact: true }).count(),
    1,
  );
  await page.getByRole("button", { name: "柱状图", exact: true }).click();
  await page.getByRole("img", { name: "查询结果柱状图" }).waitFor();
  const count = submitted;
  await page.getByRole("button", { name: "折线图", exact: true }).click();
  assert.equal(submitted, count);
  await page.getByText("展开完整 SQL", { exact: true }).click();
  await page
    .getByText(
      "SELECT team_name, SUM(amount) FROM v_receipt GROUP BY team_name",
      { exact: true },
    )
    .waitFor();
  await page.getByRole("button", { name: "反馈问题", exact: true }).click();
  await page.getByLabel("反馈内容").fill("测试口径反馈");
  await page.getByRole("button", { name: "提交反馈", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "反馈已记录" }).waitFor();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: path.join(__dirname, "test-results/query-desktop.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "查询历史" }).click();
  await page.getByRole("button", { name: "恢复会话" }).click();
  await page.getByText("接口测试返回的团队净回款", { exact: true }).waitFor();
  await page.getByRole("button", { name: "数据字典" }).click();
  await page.getByRole("cell", { name: "INTEGER 主键", exact: true }).waitFor();
  await page.getByRole("button", { name: "指标口径" }).click();
  await page
    .getByRole("heading", { name: "期间净回款", exact: true })
    .waitFor();
  await page.getByRole("button", { name: "新建问数" }).click();
  await page.getByLabel("问数问题或澄清回复").fill("测试失败");
  await page.getByRole("button", { name: "开始问数" }).click();
  await page.getByRole("heading", { name: "本次查询未完成" }).waitFor();
  await page.getByRole("alert").filter({ hasText: "查询编号：q-3" }).waitFor();
  await page.getByRole("button", { name: "查询历史" }).click();
  await page.getByRole("button", { name: "恢复会话" }).click();
  await page.getByRole("heading", { name: "本次查询未完成" }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "新建问数" }).click();
  await page.screenshot({
    path: path.join(__dirname, "test-results/home-mobile.png"),
    fullPage: true,
  });
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
    "窄屏不应横向溢出",
  );
  await page.setViewportSize({ width: 1280, height: 850 });
  await page.getByRole("button", { name: "退出登录" }).click();
  role = "admin";
  await page.getByLabel("账号", { exact: true }).fill("admin");
  await page.getByLabel("密码", { exact: true }).fill("test");
  await page.getByRole("button", { name: "登 录", exact: true }).click();
  await page.getByRole("button", { name: "系统配置" }).click();
  await page.getByRole("button", { name: "测试模型连接" }).click();
  await page
    .getByRole("status")
    .filter({ hasText: "模拟契约校验通过" })
    .waitFor();
  await page
    .getByLabel("配置包预览 / 粘贴 JSON")
    .fill(
      JSON.stringify({
        schema: metadata.schema,
        dictionary: metadata.dictionary,
        metrics: metadata.metrics,
      }),
    );
  await page.getByRole("button", { name: "校验并发布配置包" }).click();
  await page
    .getByRole("status")
    .filter({ hasText: "元数据配置已发布" })
    .waitFor();
  await page.getByRole("button", { name: "查询运行记录" }).click();
  await page
    .getByRole("button", { name: "查看详情", exact: true })
    .last()
    .click();
  await page.getByRole("heading", { name: "运行详情" }).waitFor();
  assert.deepEqual(errors, []);
  await browser.close();
  console.log(
    "UI回归通过：登录/角色导航/澄清/表格NULL/图表切换/SQL/反馈/历史恢复/元数据/管理连接与导入/窄屏",
  );
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
