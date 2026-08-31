import { describe, it, expect } from "vitest";
import { groupByBranch } from "./shiftGroups.js";

describe("список смены по филиалам", () => {
  it("разбивает курьеров по филиалам, сохраняя порядок", () => {
    const groups = groupByBranch([
      { employee_id: "a1", name: "Адильхан", branch_code: "10ASTKBN", branch_name: "Алиева" },
      { employee_id: "k1", name: "Ерлан", branch_code: "KAZH", branch_name: "Кажымукана" },
      { employee_id: "a2", name: "Канат", branch_code: "10ASTKBN", branch_name: "Алиева" },
    ]);
    expect(groups.map((g) => g.branch_name)).toEqual(["Алиева", "Кажымукана"]);
    expect(groups[0].employees.map((e) => e.name)).toEqual(["Адильхан", "Канат"]);
    expect(groups[1].employees.map((e) => e.name)).toEqual(["Ерлан"]);
  });

  it("один филиал — одна группа", () => {
    const groups = groupByBranch([
      { employee_id: "a1", name: "Адильхан", branch_code: "10ASTKBN", branch_name: "Алиева" },
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].employees).toHaveLength(1);
  });

  it("строки без филиала не теряются", () => {
    const groups = groupByBranch([{ employee_id: "x", name: "Без филиала" }]);
    expect(groups).toHaveLength(1);
    expect(groups[0].branch_name).toBe("");
    expect(groups[0].employees.map((e) => e.name)).toEqual(["Без филиала"]);
  });

  it("пустая смена — пустой список групп", () => {
    expect(groupByBranch([])).toEqual([]);
  });
});
