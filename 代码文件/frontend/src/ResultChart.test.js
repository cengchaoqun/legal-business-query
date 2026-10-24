import { describe, expect, it } from "vitest";
import { h } from "vue";
import { renderToString } from "@vue/server-renderer";
import ResultChart from "./ResultChart.vue";

async function renderChart(pointCount, seriesCount) {
  return renderToString(
    h(ResultChart, {
      kind: "bar",
      chart: {
        x: Array.from({ length: pointCount }, (_, i) => "分类" + i),
        series: Array.from({ length: seriesCount }, (_, i) => ({
          name: "指标" + i,
          data: Array.from({ length: pointCount }, (_, j) => String(j + 1)),
        })),
      },
    }),
  );
}

describe("密集图表布局", () => {
  it("200个分类与多系列保留正柱宽和独立间距，并扩展可滚动画布", async () => {
    for (const seriesCount of [1, 3]) {
      const markup = await renderChart(200, seriesCount);
      const bars = [
        ...markup.matchAll(/<rect\b[^>]*\bx="([^"]+)"[^>]*\bwidth="([^"]+)"/g),
      ].map((m) => ({ x: Number(m[1]), width: Number(m[2]) }));
      expect(bars).toHaveLength(200 * seriesCount);
      expect(Math.min(...bars.map((b) => b.width))).toBeGreaterThanOrEqual(12);
      expect(bars[1].x - bars[0].x).toBeGreaterThanOrEqual(
        (bars[0].width + 3) * seriesCount,
      );
      expect(
        Number(markup.match(/viewBox="0 0 ([\d.]+) 310"/)[1]),
      ).toBeGreaterThan(740);
    }
  });
  it("常见少量数据保留原有画布尺寸", async () => {
    expect(await renderChart(3, 1)).toContain('viewBox="0 0 740 310"');
  });
});
