# MySQL 演示沙箱初始化核验

核验日期：2026-10-22。范围仅为 `legal-ask-mysql` 对应的本机 `127.0.0.1:3308` MySQL 8.0 沙箱；未操作其他 MySQL 实例或其他容器。

## 初始化结果

- 已创建独立的 `legal_app` 与 `legal_analytics` schema。
- 分析 schema 使用 `交付方案/微调数据` 中的 fixture，包含六张 `demo_*` 底表、六个 `v_*` 视图和 `sync_watermark`。
- 应用 schema 包含 `app_user`、`auth_session`、`conversation`、`query_run`、`query_feedback` 五张表。
- `legal_app` 数据库账号仅具有应用 schema 的 SELECT、INSERT、UPDATE、DELETE、CREATE、INDEX、REFERENCES 权限。
- `legal_reader` 仅获得批准六视图与 `sync_watermark` 的 SELECT 权限，没有底表、系统库、SHOW VIEW 和写权限。
- admin 与 analyst 应用账号使用随机密码；root 初始空密码已更换为随机密码。
- 凭据仅保存在本地 `.runtime/demo-credentials.txt` 和 `.runtime/root-credentials.txt`；`.env` 使用显式 `MODEL_MODE=mock`。上述文件均由 `.gitignore` 排除，不在本核验记录中展示密码或完整 DSN。

## 实际执行验证

|检查|结果|
|---|---|
|`bootstrap_demo.py --help`|退出码 0，中文参数说明正常|
|首次初始化|退出码 0|
|再次初始化，传入不同水位|退出码 0；已有数据与水位保留|
|`docker compose config --quiet`|退出码 0|
|真实 MySQL 上认证正确及错误密码|通过|
|真实 MySQL 上创建、读取和删除认证 session|通过|
|真实 MySQL 上创建会话、保存查询、推进阶段及反馈|通过|
|相同 `client_request_id` 重试|返回原 query_id，不重复创建|
|同会话第二个新请求|StorageConflict 拒绝|
|查询成功后再尝试写 failed|原 succeeded 终态保留|
|查询完成释放会话 active_query_id|通过|
|reader `SELECT COUNT(*) FROM v_case`|成功，演示数据 7 行|
|reader 读取 sync_watermark|成功，保留 `2026-10-22T09:00:00+08:00`|
|reader 尝试 `INSERT INTO v_case ... WHERE 1=0`|MySQL 1142 拒绝，无业务数据写入|
|reader 尝试 `SELECT * FROM demo_case LIMIT 0`|MySQL 1142 拒绝|
|reader 尝试 `SELECT User FROM mysql.user LIMIT 0`|MySQL 1142 拒绝|
|两个独立 MySQL 连接竞争同一会话|不同请求仅一个创建成功；相同请求返回同一 query_id，恰好一次 created=true|
|reader 对视图执行 EXPLAIN|MySQL 1345 拒绝；试授六视图 SHOW VIEW 后仍拒绝，已撤销该无效权限|
|SQLGuard → Analytics：QNEW / legal_001|4 件；与参考结果逐值一致|
|SQLGuard → Analytics：QNET / legal_005|`17000.00` 元；与参考结果逐值一致，Decimal 作为字符串输出|
|SQLGuard → Analytics：hours / legal_006|`27.00` 小时；与参考结果逐值一致|
|SQLGuard → Analytics：两事实预聚合 / legal_045|案件 1：9000.00 元/15.00 小时；案件 2：5000.00 元/8.00 小时；案件 3：3000.00 元/4.00 小时；与参考结果逐值一致|

## 执行计划预检的设计调整

MySQL 对视图执行 EXPLAIN 需要视图底表的 SELECT 权限；SQL SECURITY DEFINER 视图和单独授 SHOW VIEW 均不能消除此要求。该事实在本沙箱实际复现，并与 [MySQL 官方问题说明](https://bugs.mysql.com/bug.php?id=111580) 一致。

本项目优先保留“分析账号只能读取批准视图”的数据库权限边界，因此没有给 reader 增加底表权限，也没有引入高权限存储过程。当 EXPLAIN 返回特定的 1345 时，查询结果显式标记 `execution_checks.explain=unavailable_view_permissions`，表示本次没有完成执行计划预检，再在 SQL AST 校验、只读事务、服务器执行超时、结果行数与字节限制下执行。其他 EXPLAIN 错误继续失败，不静默绕过。

上述四类查询均实际通过 SQLGuard 后调用 Analytics.execute 成功执行，并携带 `execution_checks.read_only=true`、`execution_checks.ast_validated=true` 和该预检限制标记；成功执行不代表完成了 EXPLAIN。

## 可重复初始化方式

全新 Compose 部署先生成随机本地配置，再启动此专属实例，最后初始化：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/bootstrap_demo.py --prepare-env
docker compose up -d
.\.venv\Scripts\python.exe -X utf8 scripts/bootstrap_demo.py
```

对于已经运行、尚未配置密码的 3308 专属沙箱，直接执行最后一条初始化命令。初始化成功后，重跑会自动读取 `.runtime/root-credentials.txt`；已有分析数据不会重建，已有应用用户密码不会重设。

需要使用其他 root 凭据时，通过进程环境变量 `BOOTSTRAP_ROOT_URL` 注入 SQLAlchemy/PyMySQL URL；脚本强制限定本机 3308，避免误连其他数据库。人工维护的现有 `.env` 不会被覆盖。不要在共享终端、聊天或版本库中展示真实 URL 和凭据文件内容。
