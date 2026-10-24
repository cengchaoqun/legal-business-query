# DeepSeek真实模型验收记录

日期：2026-10-23。范围：本项目模拟业务数据，真实DeepSeek服务与真实MySQL 8；不代表真实机构数据或生产验收。

## 接入状态

- 使用官方`https://api.deepseek.com/chat/completions`，模型`deepseek-flash`。
- 密钥取自本机已有`DEEPSEEK_API_KEY`环境变量，写入被忽略的项目`.env`。未写入源码、前端、报告或测试数据。
- `MODEL_MODE=http`，开启`MODEL_JSON_MODE=true`、`MODEL_THINKING=disabled`。官方模型列表鉴权HTTP 200，真实ModelClient输出契约检查通过。
- 使用现有ModelClient，不引入训练平台或SDK依赖。通用微调服务可关闭两个扩展参数，业务代码无需改动。
- 此次接入的是DeepSeek通用模型，本项目的专用微调训练及权重部署尚未进行。

## 真实模型与MySQL联合验收

执行：`.\.venv\Scripts\python.exe -X utf8 -u scripts/accept_deepseek.py`。

2026-10-23 09:03:04完成的一轮：**17/17通过**，实际模型调用14次，实际分析SQL执行11次，自动修复0次；模型单次响应平均1.849秒，最大2.422秒。单轮小样本结果不能推断整体准确率或服务可用性。

|验收项|核验结果|
|---|---|
|2025年新收案件|4件，按受理时间、排除撤销|
|2025年净回款|17000元，扣除退款、排除未确认/作废流水|
|2025年有效工时|27小时，仅APPROVED、按工作发生时间|
|缺少净回款统计时间|真实模型返回clarify|
|补充2025全年|继续查询返回17000元|
|追问劳动团队、按月|继承期间与指标，返回2025-06、5000元|
|结案率歧义|后端先澄清口径，此步未调用模型|
|补充期间收结比|75%，2025年结案3/新收4|
|2025年新收截至2026-01-01结案率|50%，与期间收结比区分|
|同队列截至日回款率|40%，分母为队列签约费|
|2030年期间收结比|返回NULL，不伪造0%|
|案件回款与工时|先分别聚合，案件1回款9000元、工时15小时|
|删除案件、查询系统账号密码|均refuse，未执行分析SQL|
|张律师姓名不明|后端澄清，不猜测姓名|
|相同client_request_id重复提交|同一query_id，未新增模型或SQL执行|
|普通用户管理接口|403|

完整问题、模型JSON、SQL、运行编号、结果及耗时见[结构化验收记录](deepseek-acceptance.json)。该脚本未替换模型或数据库，只包装计数和记录；sql/clarify/refuse由真实模型或既有后端前置规则产生。

首轮为9/17通过，见`deepseek-acceptance-initial.json`。失败原因主要是模型使用了静态校验器未支持的等价表达。随后明确单分支退款CASE、标量期间收结比、队列比例等可证明SQL子集，并向一次修复传递被拒SQL；**没有放宽对象、关联、函数或只读安全规则**。最终17/17为调整后的回归样例，不能作为独立留出集准确率。

## 浏览器和环境状态

真实浏览器首次已执行净回款、图表切换、SQL展开、团队月度追问、历史恢复、口径澄清、危险问题拒绝、双角色登录及管理员模型调用。管理员模型测试响应HTTP 200，脚本因中英文括号选择器不一致停止；选择器已修正。

完整浏览器重跑尚未验收通过：本机Docker/WSL反复出现失联，MySQL握手超时、Docker API无响应。`/api/health`只表示进程存活，不能代表数据库可用；不可据此称系统验收完成。当前`frontend/test-results/live-report.json`如仍标mock，是2026-10-22历史报告，不作为本轮成功证据。`live-home.png`、`live-result.png`已有本轮截图，其他旧截图不得混用。

曾按实际错误备份Docker的临时IPC目录并重启其专属WSL；没有删除、重建数据库容器或数据卷。短暂恢复期间完成了上述真实联合验收，但持续稳定性仍未通过。需先解决宿主Docker/WSL运行问题，再重跑全浏览器与MySQL回归。

应用存储已增加MySQL连接、读写超时，避免宿主数据库失联时接口无限等待。连接池及后端统一错误处理继续返回脱敏错误。该修改不能修复Docker宿主故障。

交付时已停止持续等待数据库的本轮API进程。恢复Docker/MySQL后需按README重新启动后端；不把仍存活的前端页面或health接口视为可运行验收通过。

前端本轮11项单元测试通过，生产构建通过。完整MySQL工程回归在环境失联后中断，未获得本轮全套通过结果；2026-10-22历史通过记录不冒充本轮结果。

最终后端基础回归：135 passed、11 skipped、1 warning，37.32秒。11个跳过项需要显式开启MySQL环境；唯一警告为Starlette的httpx测试客户端未来兼容提醒。这135项包含夹具及SQLite兼容验证，不能合并计为135项真实DeepSeek或MySQL验收。

官方调用参数核对：[API入口与当前模型名称](https://api-docs.deepseek.com/)、[JSON输出配置](https://api-docs.deepseek.com/guides/json_mode/)、[思考模式开关](https://api-docs.deepseek.com/guides/thinking_mode/)。使用JSON输出与关闭思考是本项目的配置选择。

## 复验

在PowerShell项目根目录执行，先确认Docker Desktop运行、MySQL日志出现`ready for connections`，再启动前后端；启动命令见根目录README。

```powershell
Set-Location -LiteralPath 'C:\Users\cengc\Desktop\项目实战\问数'
docker start legal-ask-mysql
docker logs --tail 12 legal-ask-mysql
.\.venv\Scripts\python.exe -X utf8 -u scripts/accept_deepseek.py
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$env:LEGAL_MYSQL_TEST='1'
.\.venv\Scripts\python.exe -X utf8 -m pytest --rootdir=. --noconftest --basetemp=.runtime/pytest-mysql-check backend/tests -q
$env:PLAYWRIGHT_MODULE='C:/Users/cengc/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'
$env:PLAYWRIGHT_CHROMIUM_EXECUTABLE='C:/Users/cengc/AppData/Local/ms-playwright/chromium-1223/chrome-win64/chrome.exe'
node frontend/live-smoke.cjs
```

待办：Docker/WSL稳定运行、完整浏览器验收、独立问题集准确率评估、专用微调模型替换验收及真实业务数据授权接入。模型故障、危险SQL、结果限制等夹具测试属于工程回归，不等同真实服务故障注入或生产验证。
