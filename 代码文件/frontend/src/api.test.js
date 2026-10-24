import { describe, it, expect, vi } from "vitest";
import { createApi, followQuery, displayValue } from "./api.js";
describe("查询与认证契约", () => {
  it("同源cookie与中文错误保留查询编号", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue({
        ok: false,
        status: 504,
        json: async () => ({
          detail: { message: "模型响应超时", query_id: "q-7" },
        }),
      });
    const api = createApi(fetch);
    await expect(api("/queries/q-7")).rejects.toThrow(
      "模型响应超时 · 查询编号：q-7",
    );
    expect(fetch.mock.calls[0][1].credentials).toBe("same-origin");
  });
  it("401触发退出且不继续使用登录状态", async () => {
    const logout = vi.fn();
    const api = createApi(
      vi
        .fn()
        .mockResolvedValue({ ok: false, status: 401, json: async () => ({}) }),
      logout,
    );
    await expect(api("/me")).rejects.toThrow("登录已过期");
    expect(logout).toHaveBeenCalledOnce();
  });
  it("503失败响应保留完整查询以供结果卡与历史展示", async () => {
    expect.assertions(2);
    const query = {
      query_id: "q-failed",
      status: "failed",
      error_code: "MODEL_UNAVAILABLE",
      error_message: "模型服务暂不可用",
    };
    const api = createApi(
      vi
        .fn()
        .mockResolvedValue({ ok: false, status: 503, json: async () => query }),
    );
    try {
      await api("/conversations/c1/queries", { method: "POST" });
    } catch (error) {
      expect(error.message).toBe("模型服务暂不可用 · 查询编号：q-failed");
      expect(error.query).toEqual(query);
    }
  });
  it("processing只轮询GET，遇澄清即停止", async () => {
    const api = vi
      .fn()
      .mockResolvedValueOnce({ query_id: "q1", status: "processing" })
      .mockResolvedValueOnce({ query_id: "q1", status: "clarify" });
    const sleep = vi.fn().mockResolvedValue();
    const result = await followQuery(
      { query_id: "q1", status: "processing" },
      api,
      { sleep },
    );
    expect(result.status).toBe("clarify");
    expect(api.mock.calls.map((c) => c[0])).toEqual([
      "/queries/q1",
      "/queries/q1",
    ]);
    expect(sleep).toHaveBeenCalledWith(1000);
  });
  it("35秒后终止轮询并保留原请求，不自动重发", async () => {
    const api = vi
      .fn()
      .mockResolvedValue({ query_id: "q1", status: "processing" });
    await expect(
      followQuery({ query_id: "q1", status: "processing" }, api, {
        sleep: async () => {},
      }),
    ).rejects.toThrow("请在查询历史中查看");
    expect(api).toHaveBeenCalledTimes(35);
  });
  it("空比例不转成0，金额字符串保持精度", () => {
    expect(displayValue(null)).toBe("—");
    expect(displayValue("123456789012345.67")).toBe("123456789012345.67");
  });
});
