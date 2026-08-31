/**
 * Группирует список смены по филиалам, сохраняя порядок появления.
 *
 * На нескольких филиалах плоский список смешивает курьеров разных депо, и
 * можно тапнуть чужое имя — привязка уедет не в тот филиал.
 * Возвращает [{ branch_code, branch_name, employees }].
 */
export function groupByBranch(shift) {
  const groups = [];
  const byCode = new Map();
  for (const emp of shift || []) {
    const code = emp.branch_code || "";
    let group = byCode.get(code);
    if (!group) {
      group = { branch_code: code, branch_name: emp.branch_name || "", employees: [] };
      byCode.set(code, group);
      groups.push(group);
    }
    group.employees.push(emp);
  }
  return groups;
}
