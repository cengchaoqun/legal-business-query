<script setup>
import { computed } from "vue";
const props = defineProps({ chart: Object, kind: String });
const colors = ["#3f6cf6", "#18a58c", "#d79731", "#895bcb"];
const series = computed(() => props.chart?.series || []);
const values = computed(() =>
  series.value
    .flatMap((s) => s.data)
    .filter((v) => v !== null)
    .map(Number)
    .filter(Number.isFinite),
);
const min = computed(() => Math.min(0, ...values.value));
const max = computed(() => Math.max(1, ...values.value));
// 分类增多时扩展画布并横向滚动，保留柱间距与标签空间，不把柱宽压成负数。
const groupWidth = computed(() =>
  Math.max(
    600 / Math.max(1, props.chart.x.length),
    Math.max(1, series.value.length) * 22,
    props.chart.x.length > 12 ? 72 : 0,
  ),
);
const plotWidth = computed(
  () => groupWidth.value * Math.max(1, props.chart.x.length),
);
const canvasWidth = computed(() => plotWidth.value + 140);
const y = (v) =>
  250 - ((Number(v) - min.value) / (max.value - min.value)) * 210;
const x = (i) => 70 + (i + 0.5) * groupWidth.value;
const width = computed(() =>
  Math.min(64, (groupWidth.value * 0.8) / Math.max(1, series.value.length)),
);
const segments = (s) => {
  const parts = [];
  let current = [];
  s.data.forEach((v, i) => {
    if (v === null) {
      if (current.length) parts.push(current);
      current = [];
    } else current.push(`${x(i)},${y(v)}`);
  });
  if (current.length) parts.push(current);
  return parts;
};
</script>
<template>
  <div class="chart-scroll">
    <svg
      class="chart"
      :viewBox="`0 0 ${canvasWidth} 310`"
      :style="
        canvasWidth > 740
          ? { width: `${canvasWidth}px`, minWidth: `${canvasWidth}px` }
          : { width: '100%', minWidth: '460px' }
      "
      role="img"
      :aria-label="kind === 'line' ? '查询结果折线图' : '查询结果柱状图'"
    >
      <g v-for="tick in [0, 1, 2, 3, 4]" :key="tick">
        <line
          x1="70"
          :x2="plotWidth + 90"
          :y1="40 + tick * 52.5"
          :y2="40 + tick * 52.5"
          stroke="#e7ebf2"
        />
        <text
          x="60"
          :y="44 + tick * 52.5"
          text-anchor="end"
          fill="#7b89a4"
          font-size="11"
        >
          {{
            Number(max - ((max - min) * tick) / 4).toLocaleString("zh-CN", {
              maximumFractionDigits: 2,
            })
          }}
        </text>
      </g>
      <line
        x1="70"
        :x2="plotWidth + 90"
        :y1="y(0)"
        :y2="y(0)"
        stroke="#a8b6cd"
      />
      <g v-for="(s, si) in series" :key="s.name">
        <template v-if="kind === 'line'">
          <polyline
            v-for="(segment, i) in segments(s)"
            :key="i"
            :points="segment.join(' ')"
            fill="none"
            :stroke="colors[si % 4]"
            stroke-width="2.5"
          />
          <template v-for="(v, i) in s.data" :key="i">
            <circle
              v-if="v !== null"
              :cx="x(i)"
              :cy="y(v)"
              r="4"
              :fill="colors[si % 4]"
            >
              <title>{{ chart.x[i] }} · {{ s.name }}：{{ v }}</title>
            </circle>
          </template>
        </template>
        <template v-else>
          <template v-for="(v, i) in s.data" :key="i">
            <rect
              v-if="v !== null"
              :x="x(i) + (si - series.length / 2) * width"
              :y="Math.min(y(v), y(0))"
              :width="width - 3"
              :height="Math.max(1, Math.abs(y(v) - y(0)))"
              :fill="colors[si % 4]"
              rx="2"
            >
              <title>{{ chart.x[i] }} · {{ s.name }}：{{ v }}</title>
            </rect>
          </template>
        </template>
      </g>
      <text
        v-for="(label, i) in chart.x"
        :key="i"
        :x="x(i)"
        y="277"
        fill="#74829d"
        font-size="11"
        text-anchor="middle"
      >
        {{ label }}
      </text>
    </svg>
    <div class="legend">
      <span v-for="(s, i) in series" :key="s.name"
        ><i :style="{ background: colors[i % 4] }"></i>{{ s.name }}</span
      >
    </div>
  </div>
</template>
