# 法律经营问数前端

Vue 3 + Vite，使用原生 CSS 和 SVG 图表。界面以 `交付方案/法律问数_低保真UI.pen` 及配套 PNG 为依据，保持浅灰侧栏、白底和蓝色操作按钮。

## 启动

本次使用 Node.js 24 验证。先按根目录说明启动后端 `127.0.0.1:8000`，再执行：

```powershell
Set-Location 'C:\Users\cengc\Desktop\项目实战\问数\frontend'
npm.cmd ci
npm.cmd run dev -- --port 5173 --strictPort
```

浏览器打开 `http://127.0.0.1:5173`。Vite 将 `/api` 代理至后端，前端使用同源 HttpOnly cookie，不保存密码、模型地址或凭证。部署时应以同源反向代理转发 `/api`，不要将 Vite 开发服务器作为生产服务器。

演示账号由本地初始化流程生成，见根目录运行说明及 `.runtime/demo-credentials.txt`。不要将凭证文件提交或分发。

## 功能

- 登录与退出、角色导航、新建问数、会话追问与条件澄清。
- 表格、柱状图、折线图、完整 SQL 展开与复制、统计依据及反馈。
- 查询历史恢复、数据字典、指标口径。
- 管理员查询运行记录、只读系统配置、模型连接测试、JSON元数据包校验发布。
- 结果全部来自后端 API；首页常用问题只有问题文本。`mock` 模型和演示数据都有醒目标识，不能视为真实模型推理效果。
- 同步请求等待预算为32秒；已有 `processing` 查询只发GET恢复，每秒最多一次，总等待最多35秒，终态立即停止，不自动重新提交。
- HTTP503/504错误保留查询编号与失败记录；401清理登录状态。金额字符串保持精度，NULL显示为 `—`。

## 验证

```powershell
npm.cmd test
npm.cmd run build
npm.cmd audit
```

`src/api.test.js` 覆盖cookie、401、503查询保留、processing终态停止、35次轮询上限、NULL与金额显示。构建输出位于 `dist/`。

浏览器回归需要 Playwright 和 Chromium。若已安装可直接运行 `npm.cmd run test:ui`。也可以通过环境变量指向已有运行时；当前机器验证命令为：

```powershell
$env:PLAYWRIGHT_MODULE = 'C:/Users/cengc/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'
$env:PLAYWRIGHT_CHROMIUM_EXECUTABLE = 'C:/Users/cengc/AppData/Local/ms-playwright/chromium-1223/chrome-win64/chrome.exe'
npm.cmd run test:ui
npm.cmd run test:live
```

运行前需保持5173服务可访问；`test:live`另需实际后端8000及本地演示账号文件。普通沙箱若阻止本机网络或浏览器进程，应通过宿主允许的执行方式运行，不要用测试桩替代真实联调结论。

- `ui-smoke.cjs` 使用独立API测试桩，覆盖登录、角色入口、澄清、NULL、图表切换不发请求、完整SQL、反馈、HTTP503与历史恢复、字典指标、管理连接与导入、390px窄屏。
- `live-smoke.cjs` 读取本地演示密码且不输出密码，通过真实浏览器及后端查询MySQL演示视图，验证净回款、条件继承追问、澄清、新会话、危险问题拒绝、历史恢复、两种角色及管理员页面。
- 截图和真实联调报告位于 `test-results/`。`home-*`、`query-desktop`为API桩截图，`live-*`为真实后端截图；`live-report.json`记录实际查询编号和状态。

2026-10-22的历史验收使用显式`mock`模型。2026-10-23已配置DeepSeek真实HTTP服务；`live-smoke.cjs`从后端读取实际模式并写入报告。当前验收结果以根目录`docs`中的最新模型验收记录为准。

## 文件组织

`App.vue` 管理登录、会话与页面；`QueryCard.vue` 展示结果和依据；`ResultChart.vue` 使用同一结果数据绘图，负值基线与NULL断点均保留；`api.js` 负责请求错误和受限轮询。未引入模型训练、任意SQL编辑或在线凭证保存页面。
