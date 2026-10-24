<script setup>
import { computed, onMounted, ref } from "vue";
import { createApi, followQuery, displayValue as value } from "./api";
import QueryCard from "./QueryCard.vue";
const user = ref(null),
  checking = ref(true),
  page = ref("query"),
  error = ref(""),
  notice = ref(""),
  busy = ref(false),
  loading = ref(false);
const username = ref(""),
  password = ref(""),
  question = ref(""),
  conversation = ref(null),
  queries = ref([]),
  history = ref([]),
  metadata = ref(null),
  runs = ref([]),
  system = ref(null),
  search = ref(""),
  selectedTable = ref(""),
  stage = ref(""),
  expandedRun = ref(null),
  importText = ref(""),
  importName = ref(""),
  testing = ref(false),
  importing = ref(false);
const nav = [
  { id: "query", name: "经营问数", icon: "◈" },
  { id: "history", name: "查询历史", icon: "◷" },
  { id: "dictionary", name: "数据字典", icon: "▤" },
  { id: "metrics", name: "指标口径", icon: "≡" },
  { id: "runs", name: "查询运行记录", icon: "▥", admin: true },
  { id: "system", name: "系统配置", icon: "⚙", admin: true },
];
const title = computed(
  () => nav.find((n) => n.id === page.value)?.name || "经营问数",
);
const api = createApi(globalThis.fetch, () => {
  user.value = null;
  queries.value = [];
  conversation.value = null;
  metadata.value = null;
  history.value = [];
  runs.value = [];
  system.value = null;
});
const post = (path, data = {}) =>
  api(path, { method: "POST", body: JSON.stringify(data) });
const tables = computed(() =>
  Object.entries(metadata.value?.schema?.tables || {}),
);
const fields = computed(() => {
  const list = tables.value
    .filter(([name]) => !selectedTable.value || name === selectedTable.value)
    .flatMap(([table, detail]) =>
      Object.entries(detail.columns || {}).map(([name, description]) => ({
        table,
        name,
        description:
          typeof description === "object" ? value(description) : description,
        grain: detail.grain,
      })),
    );
  return list.filter((f) =>
    JSON.stringify(f).toLowerCase().includes(search.value.toLowerCase()),
  );
});
const metrics = computed(() =>
  (metadata.value?.metrics || []).filter((m) =>
    JSON.stringify(m).toLowerCase().includes(search.value.toLowerCase()),
  ),
);
const filteredHistory = computed(() =>
  history.value.filter((item) =>
    JSON.stringify(item).toLowerCase().includes(search.value.toLowerCase()),
  ),
);
const examples = [
  {
    title: "案件分析",
    subtitle: "新收、在办、结案",
    question: "2025年全团队新收多少案件？",
  },
  {
    title: "回款分析",
    subtitle: "入账、退款、净回款",
    question: "按案件归属团队统计2025年的净回款",
  },
  {
    title: "律师工作量",
    subtitle: "已审核工时、主办量",
    question: "2025年各律师有效工时排名",
  },
];
const stages = {
  received: "已接收问题",
  retrieving: "正在核实元数据与口径",
  generating: "正在生成查询",
  validating: "正在校验只读 SQL",
  executing: "正在查询",
  formatting: "正在整理结果",
};
async function login() {
  error.value = "";
  busy.value = true;
  try {
    await post("/auth/login", {
      username: username.value,
      password: password.value,
    });
    password.value = "";
    await loadIdentity();
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function loadIdentity() {
  user.value = await api("/me");
  metadata.value = await api("/metadata");
  await loadHistory();
}
async function logout() {
  try {
    await post("/auth/logout");
    user.value = null;
    queries.value = [];
    history.value = [];
    runs.value = [];
    system.value = null;
    metadata.value = null;
    conversation.value = null;
    password.value = "";
  } catch (e) {
    error.value = e.message;
  }
}
async function loadHistory() {
  history.value = (await api("/conversations")).items || [];
}
async function navigate(id) {
  if (busy.value) return;
  page.value = id;
  search.value = "";
  error.value = "";
  notice.value = "";
  loading.value = true;
  try {
    if (id === "history") await loadHistory();
    if (id === "runs")
      runs.value = (await api("/admin/query-runs")).items || [];
    if (id === "system") system.value = await api("/admin/system/status");
  } catch (e) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
}
function newQuery() {
  if (busy.value) return;
  conversation.value = null;
  queries.value = [];
  question.value = "";
  page.value = "query";
  error.value = "";
  notice.value = "";
}
async function submit() {
  if (busy.value || !question.value.trim()) return;
  const text = question.value.trim();
  busy.value = true;
  error.value = "";
  notice.value = "";
  stage.value = "正在生成查询";
  let initial = null;
  const clientId = crypto.randomUUID();
  try {
    if (!conversation.value)
      conversation.value = (await post("/conversations")).conversation_id;
    initial = await post(
      "/conversations/" + encodeURIComponent(conversation.value) + "/queries",
      { client_request_id: clientId, question: text },
    );
    const final = await followQuery(initial, api, {
      onProgress: (q) => {
        stage.value = stages[q.stage] || "正在恢复查询";
      },
    });
    queries.value.push({ ...final, question: final.question || text });
    question.value = "";
    await loadHistory();
  } catch (e) {
    error.value = e.message;
    if (e.query) {
      queries.value.push({ ...e.query, question: e.query.question || text });
      question.value = "";
      await loadHistory().catch(() => {});
    } else if (initial?.status === "processing")
      queries.value.push({ ...initial, question: text });
  } finally {
    busy.value = false;
    stage.value = "";
  }
}
async function restore(item) {
  loading.value = true;
  busy.value = true;
  error.value = "";
  notice.value = "";
  page.value = "query";
  try {
    const response = await api(
      "/conversations/" +
        encodeURIComponent(item.id || item.conversation_id) +
        "/queries",
    );
    conversation.value = item.id || item.conversation_id;
    queries.value = response.items || [];
    question.value = "";
    const pending = queries.value.find((q) => q.status === "processing");
    if (pending) {
      busy.value = true;
      try {
        const final = await followQuery(pending, api, {
          onProgress: (q) => {
            stage.value = stages[q.stage] || "正在恢复查询";
          },
        });
        queries.value = queries.value.map((q) =>
          q.query_id === final.query_id ? final : q,
        );
      } finally {
        busy.value = false;
        stage.value = "";
      }
    }
  } catch (e) {
    error.value = e.message;
  } finally {
    loading.value = false;
    busy.value = false;
  }
}
async function feedback(id, data) {
  try {
    await post("/queries/" + encodeURIComponent(id) + "/feedback", data);
    notice.value = "反馈已记录，等待人工审核。查询结果与指标配置保持原版本。";
  } catch (e) {
    error.value = e.message + " · 查询编号：" + id;
  }
}
async function testConnection() {
  testing.value = true;
  error.value = "";
  notice.value = "";
  try {
    const response = await post("/admin/model/test");
    if (response.ok)
      notice.value = response.message + "（" + response.mode + "）";
    else error.value = response.message;
  } catch (e) {
    error.value = e.message;
  } finally {
    testing.value = false;
  }
}
async function readFile(event) {
  error.value = "";
  const file = event.target.files?.[0];
  if (!file) return;
  try {
    if (file.size > 2000000) throw new Error("配置包超过2MB，请缩小后重试");
    importText.value = await file.text();
    importName.value = file.name;
    JSON.parse(importText.value);
  } catch (e) {
    importText.value = "";
    importName.value = "";
    error.value =
      e instanceof SyntaxError ? "JSON格式无效，请检查配置包" : e.message;
  }
}
async function importMetadata() {
  importing.value = true;
  error.value = "";
  notice.value = "";
  try {
    let payload;
    try {
      payload = JSON.parse(importText.value);
    } catch {
      throw new Error("JSON格式无效，请检查配置包");
    }
    if (
      !payload.schema ||
      !payload.dictionary ||
      !Array.isArray(payload.metrics)
    )
      throw new Error("配置包必须包含 schema、dictionary 和 metrics 数组");
    const response = await post("/admin/metadata/import", payload);
    metadata.value = await api("/metadata");
    system.value = await api("/admin/system/status");
    notice.value =
      "元数据配置已发布：" + (response.version || metadata.value.version);
    importText.value = "";
    importName.value = "";
  } catch (e) {
    error.value = e.message;
  } finally {
    importing.value = false;
  }
}
const statusNames = {
  processing: "处理中",
  clarify: "待澄清",
  refuse: "已拒绝",
  succeeded: "已完成",
  failed: "失败",
};
onMounted(async () => {
  try {
    await loadIdentity();
  } catch (e) {
    if (user.value) error.value = e.message;
  } finally {
    checking.value = false;
  }
});
</script>
<template>
  <div v-if="checking" class="boot" role="status">正在连接法律问数工作台…</div>
  <div v-else-if="!user" class="login-page">
    <section class="login-intro">
      <div class="brand-mark">掌</div>
      <p class="eyebrow">LEGAL BUSINESS INTELLIGENCE</p>
      <h1>让每一次经营判断<br />都有数据依据。</h1>
      <p>案件 · 回款 · 律师工时</p>
      <span class="login-caption">掌柜 · 法律问数 / 单机构只读分析工作台</span>
    </section>
    <form class="login-form" @submit.prevent="login">
      <p class="eyebrow">欢迎使用</p>
      <h2>登录法律问数</h2>
      <p class="muted">使用机构分配的内部账号</p>
      <label
        >账号<input
          v-model="username"
          autocomplete="username"
          required
          placeholder="请输入账号" /></label
      ><label
        >密码<input
          v-model="password"
          type="password"
          autocomplete="current-password"
          required
          placeholder="请输入密码"
      /></label>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <button class="primary" :disabled="busy">
        {{ busy ? "正在登录…" : "登 录" }}
      </button>
      <p class="muted small-text">
        首次部署可用演示账号见项目说明。账号权限由服务端验证，密码不会保存在浏览器。
      </p>
    </form>
  </div>
  <div v-else class="app-shell">
    <aside class="sidebar">
      <div class="brand">掌柜 <span>·</span> 法律问数</div>
      <p class="side-caption">案件经营分析工作台</p>
      <button class="primary new-button" :disabled="busy" @click="newQuery">
        ＋ 新建问数
      </button>
      <nav aria-label="主导航">
        <template v-for="n in nav" :key="n.id"
          ><button
            v-if="!n.admin || user.role === 'admin'"
            :class="{ active: page === n.id }"
            :disabled="busy"
            @click="navigate(n.id)"
          >
            <span class="nav-icon" aria-hidden="true">{{ n.icon }}</span
            >{{ n.name }}<span v-if="n.admin" class="admin-dot"></span></button
        ></template>
      </nav>
      <div class="sidebar-bottom">
        <strong
          >{{ user.username }} ·
          {{ user.role === "admin" ? "管理员" : "统计员" }}</strong
        >
        <p>{{ user.organization_id }} / 只读查询</p>
        <button class="link" :disabled="busy" @click="logout">
          退出登录 ↗
        </button>
      </div>
    </aside>
    <div class="workspace">
      <header class="topbar">
        <span>{{ title }}</span>
        <div class="topbar-right">
          <span v-if="metadata?.synthetic" class="mode-badge">演示数据</span
          ><span
            class="mode-badge"
            :class="{ mock: metadata?.model_mode === 'mock' }"
            >{{
              metadata?.model_mode === "mock"
                ? "MOCK 模型 · 规则演示"
                : metadata?.model_mode === "http"
                  ? "HTTP 模型服务"
                  : "模型状态未知"
            }}</span
          ><span class="avatar">{{
            user.username?.slice(0, 1).toUpperCase()
          }}</span
          ><button class="mobile-logout link" :disabled="busy" @click="logout">
            退出
          </button>
        </div>
      </header>
      <main>
        <div v-if="error" class="error global-message" role="alert">
          {{ error
          }}<button class="link" @click="error = ''" aria-label="关闭错误提示">
            ×
          </button>
        </div>
        <div v-if="notice" class="success global-message" role="status">
          {{ notice
          }}<button class="link" @click="notice = ''" aria-label="关闭提示">
            ×
          </button>
        </div>
        <div v-if="metadata?.model_mode === 'mock'" class="demo-strip">
          演示模式：当前使用规则模拟模型{{
            metadata?.synthetic ? "与演示数据" : ""
          }}，用于验证流程；不代表真实模型推理能力。{{
            metadata?.synthetic ? "演示数值不代表真实律所经营数据。" : ""
          }}
        </div>
        <div v-if="loading" class="loading" role="status">正在读取数据…</div>
        <section v-if="page === 'query'">
          <div class="page-title">
            <div>
              <p class="eyebrow">BUSINESS INSIGHT</p>
              <h1>
                {{
                  queries.length
                    ? "经营问数 · 对话与结果"
                    : "用一句话，了解案件经营情况"
                }}
              </h1>
              <p class="muted">
                围绕案件、净回款与律师工时提问，结果附统计口径与查询依据。
              </p>
            </div>
            <span v-if="conversation" class="subtle session-tag"
              >会话 {{ conversation.slice(0, 8) }}</span
            >
          </div>
          <div class="query-list">
            <QueryCard
              v-for="q in queries"
              :key="q.query_id"
              :query="q"
              @feedback="feedback"
            />
          </div>
          <form class="composer" @submit.prevent="submit">
            <label class="sr-only" for="question">问数问题或澄清回复</label
            ><textarea
              id="question"
              v-model="question"
              :disabled="busy"
              required
              maxlength="2000"
              :placeholder="
                queries.at(-1)?.status === 'clarify'
                  ? '请补充上述统计条件，例如：期间收结比'
                  : queries.length
                    ? '继续追问，例如：只看劳动团队，按月展示'
                    : '例如：按案件归属团队统计2025年的净回款，扣除已确认退款'
              "
              @keydown.ctrl.enter.prevent="submit"
            ></textarea>
            <div class="composer-footer">
              <span>{{
                busy
                  ? stage
                  : queries.length
                    ? "同一会话继承已确认条件 · Ctrl + Enter 发送"
                    : "请尽量说明时间范围与统计指标 · Ctrl + Enter 发送"
              }}</span
              ><button class="primary" :disabled="busy || !question.trim()">
                {{
                  busy
                    ? "处理中…"
                    : queries.length
                      ? "继续问数 ↗"
                      : "开始问数 ↗"
                }}
              </button>
            </div>
          </form>
          <template v-if="!queries.length"
            ><h3 class="section-heading">
              常用问题 <span>选择示例，按需修改</span>
            </h3>
            <div class="example-grid">
              <article
                v-for="(example, i) in examples"
                :key="example.title"
                class="example-card"
              >
                <span class="example-number">0{{ i + 1 }}</span>
                <h3>{{ example.title }}</h3>
                <p class="muted">{{ example.subtitle }}</p>
                <p>{{ example.question }}</p>
                <button :disabled="busy" @click="question = example.question">
                  使用此问题 ↗
                </button>
              </article>
            </div></template
          >
          <div class="data-note">
            当前数据范围：{{ user.scope_id }} · 仅支持经营统计<span
              v-if="metadata?.data_watermark"
            >
              · 数据水位 {{ metadata.data_watermark }}</span
            ><span v-if="metadata?.version">
              · 口径版本 {{ metadata.version }}</span
            >
          </div>
          <div v-if="history.length && !queries.length" class="recent">
            <span>最近查询</span
            ><button
              v-for="item in history.slice(0, 3)"
              :key="item.id"
              class="link"
              :disabled="busy"
              @click="restore(item)"
            >
              {{ item.title || "未命名会话" }} ↗
            </button>
          </div>
        </section>
        <section v-else-if="page === 'history'">
          <div class="page-title">
            <div>
              <p class="eyebrow">YOUR CONVERSATIONS</p>
              <h1>查询历史</h1>
              <p class="muted">
                恢复原查询结果与当时口径，继续追问将使用当前数据。
              </p>
            </div>
            <button @click="loadHistory().catch((e) => (error = e.message))">
              刷新列表
            </button>
          </div>
          <input
            v-model="search"
            class="search"
            placeholder="搜索会话标题、状态或时间"
            aria-label="搜索历史"
          />
          <div class="table-wrap panel">
            <table>
              <thead>
                <tr>
                  <th>问题 / 会话</th>
                  <th>更新时间</th>
                  <th>状态</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in filteredHistory" :key="item.id">
                  <td>
                    <strong>{{ item.title || "未命名会话" }}</strong
                    ><small class="block muted">{{ item.id }}</small>
                  </td>
                  <td>{{ item.updated_at || item.created_at || "—" }}</td>
                  <td>
                    <span
                      class="status"
                      :class="item.status || item.last_status"
                      >{{
                        statusNames[item.status || item.last_status] || "已保存"
                      }}</span
                    >
                  </td>
                  <td><button @click="restore(item)">恢复会话 ↗</button></td>
                </tr>
              </tbody>
            </table>
            <div v-if="!filteredHistory.length" class="empty">
              暂无匹配的查询历史，新建问数后将在此保存。
            </div>
          </div>
        </section>
        <section v-else-if="page === 'dictionary'">
          <div class="page-title">
            <div>
              <p class="eyebrow">APPROVED DATA</p>
              <h1>数据字典</h1>
              <p class="muted">
                查看批准的分析视图、字段含义与标准值。元数据版本：{{
                  metadata?.version
                }}
              </p>
            </div>
            <span class="status succeeded">只读</span>
          </div>
          <div class="filter-row">
            <select v-model="selectedTable" aria-label="选择分析视图">
              <option value="">全部分析视图</option>
              <option v-for="[name] in tables" :key="name" :value="name">
                {{ name }}
              </option></select
            ><input
              v-model="search"
              class="search"
              placeholder="搜索字段名称或含义"
              aria-label="搜索字段"
            />
          </div>
          <div class="table-wrap panel">
            <table>
              <thead>
                <tr>
                  <th>分析视图 / 粒度</th>
                  <th>字段</th>
                  <th>类型与说明</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="field in fields" :key="field.table + field.name">
                  <td>
                    <code>{{ field.table }}</code
                    ><small class="block muted">{{ field.grain }}</small>
                  </td>
                  <td>
                    <code>{{ field.name }}</code>
                  </td>
                  <td>{{ field.description }}</td>
                </tr>
              </tbody>
            </table>
            <div v-if="!fields.length" class="empty">没有匹配的字段</div>
          </div>
          <h3 class="section-heading">批准关联</h3>
          <ul class="plain-list">
            <li
              v-for="(relation, i) in metadata?.schema?.relationships || []"
              :key="i"
            >
              {{ value(relation) }}
            </li>
          </ul>
          <details class="panel padded">
            <summary>标准值与别名</summary>
            <div
              v-for="(entries, key) in metadata?.dictionary || {}"
              :key="key"
              class="dictionary-entry"
            >
              <strong>{{ key }}</strong>
              <div v-for="(aliases, standard) in entries" :key="standard">
                <code>{{ standard }}</code> —
                {{
                  Array.isArray(aliases) ? aliases.join(" / ") : value(aliases)
                }}
              </div>
            </div>
          </details>
        </section>
        <section v-else-if="page === 'metrics'">
          <div class="page-title">
            <div>
              <p class="eyebrow">METRIC DEFINITIONS</p>
              <h1>指标口径</h1>
              <p class="muted">
                每个结果都应遵循已批准口径，时间字段、去重方式和零分母规则不可混用。
              </p>
            </div>
            <span class="status succeeded">{{ metadata?.version }}</span>
          </div>
          <input
            v-model="search"
            class="search"
            placeholder="搜索指标名称、编号或口径"
            aria-label="搜索指标"
          />
          <div class="metric-list">
            <article v-for="metric in metrics" :key="metric.id">
              <div class="metric-title">
                <h3>{{ metric.name }}</h3>
                <code>{{ metric.id }}</code>
              </div>
              <p>{{ metric.rule }}</p>
            </article>
            <div v-if="!metrics.length" class="empty">没有匹配的已批准指标</div>
          </div>
        </section>
        <section v-else-if="page === 'runs' && user.role === 'admin'">
          <div class="page-title">
            <div>
              <p class="eyebrow">QUERY AUDIT</p>
              <h1>查询运行记录</h1>
              <p class="muted">
                追踪授权机构内的执行阶段、错误与版本。记录仅供审计。
              </p>
            </div>
            <button @click="navigate('runs')">刷新记录</button>
          </div>
          <input
            v-model="search"
            class="search"
            placeholder="搜索查询编号、问题或错误"
            aria-label="搜索运行记录"
          />
          <div class="table-wrap panel">
            <table>
              <thead>
                <tr>
                  <th>查询编号 / 问题</th>
                  <th>状态</th>
                  <th>阶段 / 耗时</th>
                  <th>错误码</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="run in runs.filter((r) =>
                    JSON.stringify(r)
                      .toLowerCase()
                      .includes(search.toLowerCase()),
                  )"
                  :key="run.query_id"
                >
                  <td>
                    <code>{{ run.query_id }}</code
                    ><small class="block">{{ run.question }}</small>
                  </td>
                  <td>
                    <span class="status" :class="run.status">{{
                      statusNames[run.status]
                    }}</span>
                  </td>
                  <td>
                    {{ stages[run.stage] || run.stage
                    }}<small class="block muted">{{
                      run.elapsed_ms == null ? "—" : run.elapsed_ms + " ms"
                    }}</small>
                  </td>
                  <td>{{ run.error_code || "—" }}</td>
                  <td>
                    <button
                      @click="
                        expandedRun =
                          expandedRun?.query_id === run.query_id ? null : run
                      "
                    >
                      查看详情
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
            <div v-if="!runs.length" class="empty">暂无查询运行记录</div>
          </div>
          <template v-if="expandedRun"
            ><h3 class="section-heading">运行详情</h3>
            <QueryCard :query="expandedRun" readonly />
            <details class="panel padded">
              <summary>执行与版本信息</summary>
              <pre>{{
                JSON.stringify(
                  {
                    stage: expandedRun.stage,
                    elapsed_ms: expandedRun.elapsed_ms,
                    sql: expandedRun.sql || expandedRun.decision?.sql,
                    error_code: expandedRun.error_code,
                    error_message: expandedRun.error_message,
                    execution_checks: expandedRun.execution_checks,
                    metadata_version: expandedRun.metadata_version,
                    model_service_version: expandedRun.model_service_version,
                    confirmed_conditions: expandedRun.confirmed_conditions,
                  },
                  null,
                  2,
                )
              }}</pre>
            </details></template
          >
        </section>
        <section v-else-if="page === 'system' && user.role === 'admin'">
          <div class="page-title">
            <div>
              <p class="eyebrow">CONNECTION & CONFIGURATION</p>
              <h1>系统配置</h1>
              <p class="muted">
                部署配置只读展示；连接测试校验当前模型契约；导入元数据包发布新版本。
              </p>
            </div>
            <span class="status">管理员</span>
          </div>
          <div v-if="system" class="settings-grid">
            <section class="panel padded">
              <h3>模型服务</h3>
              <dl class="settings-dl">
                <dt>运行模式</dt>
                <dd>
                  {{
                    system.model?.mode === "mock"
                      ? "MOCK · 规则演示"
                      : system.model?.mode
                  }}
                </dd>
                <dt>模型名称</dt>
                <dd>{{ system.model?.name }}</dd>
                <dt>服务地址（只读）</dt>
                <dd>{{ system.model?.base_url || "未配置HTTP服务" }}</dd>
                <dt>配置状态</dt>
                <dd>
                  {{ system.model?.configured ? "已配置" : "未配置外部模型" }}
                </dd>
              </dl>
              <p class="muted">
                地址与凭证由部署环境维护，页面不会写入或保存凭证。
              </p>
              <button :disabled="testing" @click="testConnection">
                {{ testing ? "检查连接与契约中…" : "测试模型连接" }}
              </button>
            </section>
            <section class="panel padded">
              <h3>数据与执行边界</h3>
              <dl class="settings-dl">
                <dt>数据库方言</dt>
                <dd>{{ system.database?.dialect }}</dd>
                <dt>查询模式</dt>
                <dd>
                  {{
                    system.database?.read_only ? "只读查询" : "请检查只读配置"
                  }}
                </dd>
                <dt>执行超时</dt>
                <dd>{{ system.limits?.query_timeout_seconds }} 秒</dd>
                <dt>最大返回行数</dt>
                <dd>{{ system.limits?.max_rows }}</dd>
                <dt>元数据版本</dt>
                <dd>{{ system.metadata_version }}</dd>
                <dt>数据水位</dt>
                <dd>{{ system.data_watermark }}</dd>
                <dt>数据模式</dt>
                <dd>{{ system.synthetic ? "演示数据" : "实际数据" }}</dd>
              </dl>
            </section>
          </div>
          <section class="panel padded import-panel">
            <h3>发布元数据配置包</h3>
            <p class="muted">
              JSON对象需包含
              schema、dictionary、metrics。服务端校验成功后才替换当前版本，旧查询保留原有依据。
            </p>
            <label class="file-label"
              >选择 JSON 配置包<input
                type="file"
                accept=".json,application/json"
                @change="readFile" /></label
            ><span class="muted">{{ importName || "尚未选择文件" }}</span
            ><label class="block" for="metadata-import"
              >配置包预览 / 粘贴 JSON</label
            ><textarea
              id="metadata-import"
              v-model="importText"
              class="code-input"
              spellcheck="false"
              placeholder='{"schema": {...}, "dictionary": {...}, "metrics": [...]}'
            ></textarea
            ><button
              class="primary"
              :disabled="importing || !importText.trim()"
              @click="importMetadata"
            >
              {{ importing ? "校验并发布中…" : "校验并发布配置包" }}
            </button>
          </section>
        </section>
      </main>
      <footer>掌柜 · 法律问数<span>单机构授权范围 / 只读经营分析</span></footer>
    </div>
  </div>
</template>
