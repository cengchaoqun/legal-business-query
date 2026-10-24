<script setup>
import { ref } from "vue";
import { displayValue as value, displayConditions } from "./api";
import ResultChart from "./ResultChart.vue";
const props = defineProps({ query: Object, readonly: Boolean });
const emit = defineEmits(["feedback"]);
const view = ref("table"),
  issue = ref(false),
  feedbackType = ref("result"),
  comment = ref(""),
  copyMessage = ref("");
const names = {
  processing: "处理中",
  clarify: "待澄清",
  refuse: "已拒绝",
  succeeded: "已完成",
  failed: "失败",
};
async function copy() {
  try {
    await navigator.clipboard.writeText(
      props.query.result?.sql || props.query.sql || "",
    );
    copyMessage.value = "已复制";
  } catch {
    copyMessage.value = "复制失败，请选中下方 SQL 手动复制";
  }
}
</script>
<template>
  <article class="query-card">
    <div class="query-heading">
      <span class="status" :class="query.status">{{
        names[query.status] || query.status
      }}</span
      ><span class="query-id">查询编号：{{ query.query_id }}</span>
    </div>
    <h3 v-if="query.question">{{ query.question }}</h3>
    <template v-if="query.status === 'clarify'"
      ><h3>需要确认统计条件</h3>
      <p>{{ query.decision?.question }}</p>
      <p class="muted">{{ query.decision?.reason }}</p>
      <div v-if="query.confirmed_conditions" class="subtle">
        已确认条件：{{ displayConditions(query.confirmed_conditions) }}
      </div></template
    >
    <template v-else-if="query.status === 'refuse'"
      ><h3>当前问题无法执行</h3>
      <p>{{ query.decision?.reason || query.error_message }}</p>
      <p class="muted">
        请改为授权范围内的案件、回款或律师工时统计。
      </p></template
    >
    <template v-else-if="query.status === 'failed'"
      ><h3>本次查询未完成</h3>
      <p>{{ query.error_message || "服务暂不可用，请稍后再试" }}</p>
      <small>{{ query.error_code }}</small></template
    >
    <template v-else-if="query.result"
      ><p class="result-summary">{{ query.result.summary }}</p>
      <p v-if="query.result.warnings?.length" :class="query.result.warnings.some(w => w.includes('截断')) ? 'notice' : 'muted'">
        {{ query.result.warnings.join('；') }}
      </p>
      <div class="result-layout">
        <section class="result-main">
          <div class="tabs">
            <button
              :class="{ selected: view === 'table' }"
              @click="view = 'table'"
            >
              数据表格</button
            ><button
              v-if="query.result.chart"
              :class="{ selected: view === 'bar' }"
              @click="view = 'bar'"
            >
              柱状图</button
            ><button
              v-if="query.result.chart"
              :class="{ selected: view === 'line' }"
              @click="view = 'line'"
            >
              折线图
            </button>
          </div>
          <div v-if="view === 'table'" class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th v-for="c in query.result.table?.columns || []" :key="c">
                    {{ c }}
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(r, i) in query.result.table?.rows || []" :key="i">
                  <td v-for="c in query.result.table?.columns || []" :key="c">
                    {{ value(r[c]) }}
                  </td>
                </tr>
              </tbody>
            </table>
            <div v-if="!query.result.table?.rows?.length" class="empty">
              该条件下无记录
            </div>
          </div>
          <ResultChart v-else :chart="query.result.chart" :kind="view" />
        </section>
        <aside class="basis">
          <h3>查询依据</h3>
          <template v-if="query.result.basis"
            ><div
              v-for="metric in query.result.basis.metrics || []"
              :key="metric.id"
            >
              <strong>{{ metric.name || metric.id }}</strong>
              <p>{{ metric.rule }}</p>
            </div>
            <dl>
              <dt>已确认统计条件</dt>
              <dd>{{ displayConditions(query.result.basis.conditions) }}</dd>
              <dt>授权范围</dt>
              <dd>{{ value(query.result.basis.scope) }}</dd>
              <dt>时区</dt>
              <dd>{{ query.result.basis.timezone }}</dd>
              <dt>数据水位</dt>
              <dd>{{ query.result.basis.data_watermark }}</dd>
              <dt>元数据版本 / 模型服务版本</dt>
              <dd>
                {{ query.result.basis.metadata_version }} /
                {{ query.result.basis.model_service_version }}
              </dd>
            </dl></template
          >
          <details>
            <summary>展开完整 SQL</summary>
            <pre>{{ query.result.sql || query.sql }}</pre>
          </details>
          <button class="small" @click="copy">复制完整 SQL</button
          ><small role="status">{{ copyMessage }}</small>
        </aside>
      </div>
      <div v-if="!readonly" class="feedback">
        <span class="muted">这个结果符合预期吗？</span
        ><button
          class="small"
          @click="
            emit('feedback', query.query_id, {
              feedback_type: 'correct',
              comment: '',
            })
          "
        >
          ✓ 正确</button
        ><button class="small" @click="issue = !issue">反馈问题</button>
      </div>
      <form
        v-if="issue && !readonly"
        class="feedback-form"
        @submit.prevent="
          emit('feedback', query.query_id, {
            feedback_type: feedbackType,
            comment,
          });
          issue = false;
        "
      >
        <select v-model="feedbackType" aria-label="问题类型">
          <option value="field">字段问题</option>
          <option value="filter">筛选问题</option>
          <option value="metric">口径问题</option>
          <option value="result">结果问题</option>
          <option value="other">其他</option></select
        ><input
          v-model="comment"
          required
          maxlength="2000"
          placeholder="说明问题，反馈将进入人工审核"
          aria-label="反馈内容"
        /><button type="submit">提交反馈</button>
      </form></template
    >
    <div v-else-if="query.status === 'processing'" class="muted">
      正在处理 · {{ query.stage }}
    </div>
  </article>
</template>
