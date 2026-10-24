export function createApi(
  fetcher = globalThis.fetch,
  onUnauthorized = () => {},
) {
  return async (path, options = {}) => {
    let response;
    try {
      response = await fetcher("/api" + path, {
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        signal: AbortSignal.timeout(32000),
        ...options,
      });
    } catch (error) {
      throw new Error(
        error.name === "AbortError" || error.name === "TimeoutError"
          ? "请求等待超时，请在查询历史中查看已有结果"
          : "网络连接失败，请检查服务后到查询历史恢复已有查询",
      );
    }
    const data = await response.json().catch(() => ({}));
    if (response.status === 401) {
      onUnauthorized();
      throw new Error(
        path === "/auth/login" ? "用户名或密码错误" : "登录已过期，请重新登录",
      );
    }
    if (!response.ok) {
      const detail = data.detail || data;
      const message =
        typeof detail === "string"
          ? detail
          : Array.isArray(detail)
            ? "请求参数无效：" +
              detail.map((item) => (item.loc || []).join(".")).join("、")
            : detail.message || detail.error_message || "操作失败，请稍后重试";
      const id = detail.query_id || data.query_id;
      const error = new Error(message + (id ? " · 查询编号：" + id : ""));
      error.query = data.query_id ? data : null;
      throw error;
    }
    return data;
  };
}
export async function followQuery(
  initial,
  api,
  {
    sleep = (ms) => new Promise((r) => setTimeout(r, ms)),
    onProgress = () => {},
  } = {},
) {
  let query = initial;
  const deadline = Date.now() + 35000;
  // 只恢复既有执行；不重新POST，避免重复执行和条件并发覆盖。
  for (let i = 0; query.status === "processing" && i < 35; i++) {
    onProgress(query);
    await sleep(1000);
    const remaining = deadline - Date.now();
    if (remaining <= 0) break;
    try {
      query = await api("/queries/" + encodeURIComponent(query.query_id), {
        signal: AbortSignal.timeout(remaining),
      });
    } catch (error) {
      if (!error.message.includes(query.query_id))
        error.message += " · 查询编号：" + query.query_id;
      throw error;
    }
  }
  if (query.status === "processing")
    throw new Error(
      "查询仍在处理中，请在查询历史中查看 · 查询编号：" + query.query_id,
    );
  return query;
}
export const displayValue = (value) =>
  value === null || value === undefined
    ? "—"
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);

export function displayConditions(conditions = {}) {
  const items = [];
  if (conditions.time_range)
    items.push(`期间：${conditions.time_range.start} 至 ${conditions.time_range.end}（不含结束日）`);
  const groups = { team: '按团队', lawyer: '按律师', month: '按月', quarter: '按季度' };
  if (groups[conditions.group_by]) items.push(groups[conditions.group_by]);
  const labels = { team: '团队', lawyer: '律师', case_type: '案件类型' };
  const values = { CIVIL: '民事', LABOR: '劳动争议', CRIMINAL: '刑事', NON_LITIGATION: '非诉' };
  for (const [key, value] of Object.entries(conditions.filters || {}))
    if (labels[key]) items.push(`${labels[key]}：${values[value] || value}`);
  if (conditions.as_of) items.push(`累计截至：${conditions.as_of}零点`);
  if (conditions.unresolved_values?.length) items.push(`待核实：${conditions.unresolved_values.join('、')}`);
  if (conditions.unsupported_time) items.push('时间范围待确认');
  return items.join('；') || '暂无已确认条件';
}
