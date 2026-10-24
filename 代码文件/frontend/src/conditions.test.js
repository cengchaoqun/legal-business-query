import { expect, test } from 'vitest';
import { displayConditions } from './api';

test('仅显示用户可理解的统计条件，不输出内部状态字段', () => {
  const text = displayConditions({time_range:{start:'2025-01-01',end:'2026-01-01'},group_by:'team',filters:{team:'劳动团队'},unresolved_values:[],unsupported_time:false});
  expect(text).toContain('劳动团队');
  expect(text).toContain('按团队');
  expect(text).not.toContain('unresolved_values');
  expect(text).not.toContain('unsupported_time');
});
test('待核实条件仍展示给用户', () => {
  expect(displayConditions({unresolved_values:['团队：海事团队']})).toContain('待核实');
});
test('无已确认条件不显示空JSON', () => {
  expect(displayConditions({unresolved_values:[],unsupported_time:false})).toBe('暂无已确认条件');
});
