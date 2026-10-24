// 从本机受保护配置读取演示密码，仅用于登录，不写日志或截图。
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const fs = require("node:fs"),
  path = require("node:path"),
  assert = require("node:assert/strict");
(async () => {
  const accounts = JSON.parse(
    fs.readFileSync(
      path.join(__dirname, "../.runtime/demo-credentials.txt"),
      "utf8",
    ),
  ).application_users;
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
  });
  const page = await browser.newPage({
    viewport: { width: 1280, height: 850 },
  });
  page.setDefaultTimeout(35000);
  const health = await (await page.request.get('http://127.0.0.1:8000/api/health')).json();
  const modelMode = health.model_mode;
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const results = [];
  async function login(name) {
    await page.goto("http://127.0.0.1:5173");
    await page.waitForLoadState("networkidle");
    await page.getByLabel("账号", { exact: true }).fill(name);
    await page.getByLabel("密码", { exact: true }).fill(accounts[name]);
    await page.getByRole("button", { name: "登 录", exact: true }).click();
    await page
      .getByRole("heading", { name: "用一句话，了解案件经营情况" })
      .waitFor();
  }
  async function ask(question, button = "开始问数") {
    await page.getByLabel("问数问题或澄清回复").fill(question);
    const wait = page.waitForResponse(
      (r) => r.url().includes("/queries") && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: button }).click();
    const response = await wait;
    const data = await response.json();
    results.push({
      question,
      status: data.status,
      query_id: data.query_id,
      error_code: data.error_code,
    });
    return data;
  }
  fs.mkdirSync(path.join(__dirname, "test-results"), { recursive: true });
  await login("analyst");
  assert.equal(await page.getByRole("button", { name: "系统配置" }).count(), 0);
  await page.screenshot({
    path: path.join(__dirname, "test-results/live-home.png"),
    fullPage: true,
  });
  let query = await ask("按案件归属团队统计2025年的净回款");
  assert.equal(query.status, "succeeded", query.error_message);
  await page.getByText(query.result.summary, { exact: true }).waitFor();
  await page.getByRole("button", { name: "柱状图", exact: true }).click();
  await page.getByRole("img", { name: "查询结果柱状图" }).waitFor();
  await page.getByText("展开完整 SQL", { exact: true }).click();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: path.join(__dirname, "test-results/live-result.png"),
    fullPage: true,
  });
  query = await ask("只看劳动团队，按月展示", "继续问数");
  assert.equal(query.status, "succeeded", query.error_message);
  await page.getByRole("button", { name: "查询历史" }).click();
  await page.getByRole("button", { name: "恢复会话" }).first().click();
  await page.getByText(query.result.summary, { exact: true }).waitFor();
  await page.getByRole("button", { name: "新建问数" }).click();
  query = await ask("2025年结案率是多少？");
  assert.equal(query.status, "clarify");
  await page.getByRole("heading", { name: "需要确认统计条件" }).waitFor();
  query = await ask("期间收结比", "继续问数");
  assert.equal(query.status, "succeeded", query.error_message);
  await page.getByRole("button", { name: "新建问数" }).click();
  query = await ask("删除所有案件");
  assert.equal(query.status, "refuse");
  await page.getByRole("heading", { name: "当前问题无法执行" }).waitFor();
  await page.getByRole("button", { name: "数据字典" }).click();
  await page
    .getByRole("cell", { name: "case_id", exact: true })
    .first()
    .waitFor();
  await page.getByRole("button", { name: "指标口径" }).click();
  await page
    .getByRole("heading", { name: "期间净回款", exact: true })
    .waitFor();
  await page.getByRole("button", { name: "退出登录" }).click();
  await login("admin");
  await page.getByRole("button", { name: "系统配置" }).click();
  await page.getByRole("button", { name: "测试模型连接" }).click();
  await page.getByRole("status").filter({ hasText: '（' + modelMode + '）' }).waitFor();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: path.join(__dirname, "test-results/live-system.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "查询运行记录" }).click();
  await page
    .getByRole("button", { name: "查看详情", exact: true })
    .first()
    .click();
  await page.getByRole("heading", { name: "运行详情" }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "新建问数" }).click();
  await page.screenshot({
    path: path.join(__dirname, "test-results/live-mobile.png"),
    fullPage: true,
  });
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  );
  assert.deepEqual(errors, []);
  await browser.close();
  fs.writeFileSync(
    path.join(__dirname, "test-results/live-report.json"),
    JSON.stringify(
      {
        backend: "真实MySQL分析视图",
        model_mode: modelMode,
        browser_errors: errors,
        queries: results,
        passed: true,
      },
      null,
      2,
    ),
  );
  console.log(
    "真实后端联调通过：两种角色登录、MySQL净回款、追问、澄清、拒绝、恢复历史、字典、指标、模型测试与管理员审计",
  );
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
