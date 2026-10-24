# 掌柜 · 法律经营问数

Vue 3＋FastAPI单体＋MySQL 8。支持案件、回款、工时问数、澄清追问、SQL只读校验、表格/图表、查询历史、反馈和管理员配置。

实现依据：[模块级概要设计](交付方案/03_模块级概要设计.md)。模型只通过HTTP服务接入，项目不包含训练平台。

## 当前运行方式

2026-10-23已接入真实DeepSeek，业务接口样例17/17通过；本机Docker/WSL随后反复失联，完整浏览器与环境稳定性验收尚未通过，等待数据库的API进程已停止。请先恢复数据库再执行启动命令，详见[本轮验收记录](docs/DeepSeek真实模型验收报告.md)。

- 前端：<http://127.0.0.1:5173>
- API文档：<http://127.0.0.1:8000/docs>
- MySQL：本项目独立容器`legal-ask-mysql`，只绑定本机3308。
- 当前本机`.env`使用`MODEL_MODE=http`接入DeepSeek `deepseek-flash`；密钥从本机环境变量读取后写入私有`.env`。**统计数字来自MySQL实际执行的模拟业务数据**。
- 应用账号为`admin`和`analyst`，随机密码保存于本机`.runtime/demo-credentials.txt`的`application_users`。该文件不提交版本库，不在日志中输出密码。

## 已初始化机器启动

以下命令在PowerShell执行。打开两个终端分别运行前后端，不要重复启动已占用端口的服务。

确认数据库已启动：

```powershell
Set-Location -LiteralPath 'C:\Users\cengc\Desktop\项目实战\问数'
docker start legal-ask-mysql
```

终端一：

```powershell
Set-Location -LiteralPath 'C:\Users\cengc\Desktop\项目实战\问数'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

终端二：

```powershell
Set-Location -LiteralPath 'C:\Users\cengc\Desktop\项目实战\问数\frontend'
npm.cmd run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

## 全新环境初始化

需要Python 3.11+、Node.js 22+和可运行的Docker Desktop。首次使用：

```powershell
Set-Location -LiteralPath 'C:\Users\cengc\Desktop\项目实战\问数'
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
npm.cmd --prefix frontend ci
.\.venv\Scripts\python.exe -X utf8 scripts/bootstrap_demo.py --prepare-env
docker compose up -d
# 等待数据库health状态为healthy，再执行初始化。
docker compose ps
.\.venv\Scripts\python.exe -X utf8 scripts/bootstrap_demo.py
```

然后按前面的双终端命令启动。不要将`.env.example`里的无密码占位连接当成可用配置；bootstrap生成随机凭据。初始化可重跑，已有数据和同步水位不重置，已有账号密码不覆盖。

当前机器容器已先行独立创建并验收，因此使用`docker start legal-ask-mysql`恢复即可；全新机器才使用上述Compose流程。不要为了处理同名容器冲突删除已有数据容器。

## 接入已部署的微调模型

修改本地`.env`后重启后端：

```dotenv
MODEL_MODE=http
MODEL_BASE_URL=https://your-model-service.example/v1
MODEL_NAME=legal-sql-v1
MODEL_API_KEY=<由部署环境提供>
MODEL_JSON_MODE=false
```

服务应兼容`POST /chat/completions`的model/messages请求和choices[0].message.content响应；content必须是sql/clarify/refuse的JSON，契约见`backend/app/schemas.py`。普通微调服务保持`MODEL_JSON_MODE=false`并删除`MODEL_THINKING`，仅发送通用参数；应用不装载权重或adapter。管理员“测试模型连接”检查协议和输出契约样例，不表示通过业务准确率验收。

当前DeepSeek配置（实际密钥只保存在本机，不填入本文）：

```dotenv
MODEL_MODE=http
MODEL_BASE_URL=https://api.deepseek.com
MODEL_NAME=deepseek-flash
MODEL_JSON_MODE=true
MODEL_THINKING=disabled
```

本机密钥来源是已有的`DEEPSEEK_API_KEY`环境变量。更换密钥可在项目根目录执行以下命令，不输出密钥；配置修改后重启后端：

```powershell
.\.venv\Scripts\python.exe -c "import os; from dotenv import set_key; set_key('.env', 'MODEL_API_KEY', os.environ['DEEPSEEK_API_KEY']); print('密钥已更新')"
```

当前使用DeepSeek通用模型，尚未训练或接入本项目专用微调权重。真实模型验收命令如下；会产生少量API调用费用，并将结果写入`docs/deepseek-acceptance.json`：

```powershell
.\.venv\Scripts\python.exe -X utf8 -u scripts/accept_deepseek.py
```

## 目录

|目录|内容|
|---|---|
|backend/app|配置、身份/存储、元数据、模型客户端、SQLGuard、Analytics、编排、结果、API|
|backend/tests|权限、会话幂等、SQL安全、口径、条件继承、MySQL与模型协议测试|
|frontend/src|登录、问数、结果、历史、字典、指标、运行记录、系统配置|
|scripts|初始化及PowerShell启动脚本|
|docs|实施计划、真实MySQL核验与交付验证报告|
|交付方案|原方案、设计、pen原型和模拟微调种子|
|.runtime|本机私有凭据、版本化元数据、测试临时文件，禁止提交|

## 验证命令

后端基础检查：

```powershell
Set-Location -LiteralPath 'C:\Users\cengc\Desktop\项目实战\问数'
New-Item -ItemType Directory -Path '.runtime' -Force | Out-Null
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
.\.venv\Scripts\python.exe -X utf8 -m pytest --rootdir=. --noconftest --basetemp=.runtime/pytest-check backend/tests -q
```

包括真实MySQL检查（需先初始化数据库）：

```powershell
$env:LEGAL_MYSQL_TEST='1'
.\.venv\Scripts\python.exe -X utf8 -m pytest --rootdir=. --noconftest --basetemp=.runtime/pytest-mysql-check backend/tests -q
```

前端：

```powershell
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run build
```

浏览器回归脚本`frontend/ui-smoke.cjs`和`frontend/live-smoke.cjs`依赖Playwright及Chromium，配置方式见[前端说明](frontend/README.md)。其中ui-smoke使用API夹具；live-smoke连接真实前后端和MySQL，并记录后端实际model_mode；模型是否真实接入应结合配置和调用证据判定。有限样例不可替代完整模型准确率评估。

## 执行与权限边界

问数数据库账号只允许六授权视图及水位表的SELECT，不能读取底表、系统表或写入。应用写账号与分析账号分开。SQL校验递归核查对象、字段、函数、关联及可证明的局部指标规则；无法证明保留筛选和分组的复杂写法会失败并最多请求一次修复。

MySQL对视图EXPLAIN还要求底表权限，与上述隔离边界冲突。首期仅在错误1345时明确标记`execution_checks.explain=unavailable_view_permissions`，保持只读事务、AST检查、服务器超时和结果限制；其他预检错误继续失败。**本项目没有把这项降级写成EXPLAIN已通过。** 细节见[MySQL核验报告](docs/mysql-initialization-check.md)。

## 实施范围与尚未验证项

- 已实现八个逻辑模块和九个设计状态对应的页面；图表切换不再次调用模型。
- 当前示例只有模拟机构和六类视图，真实业务接入需ETL映射、字段/值字典审核和业务对账。
- 规则模拟只服务开发演示，支持的确定性口径不等于真实模型完整能力；复杂或未确认需求应澄清/失败而非生成估计值。
- 未提供状态变更历史、付款计划和成本，因此不计算历史时点在办、逾期应收或利润；胜诉率需要单独批准口径。
- 未做生产规模负载/故障恢复、真实机构数据审核、真实微调模型准确率验收、外网HTTPS与生产部署验收。
- 生产使用需配置HTTPS、secure cookie、部署账号生命周期和日志/历史保留策略；当前为本机可运行交付版。

实际执行结果及截图以[交付验证报告](docs/交付验证报告.md)为准。
