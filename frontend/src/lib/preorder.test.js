import { describe, it, expect, vi, afterEach } from "vitest";
import { splitPreorders, dueLabel, isPreorder } from "./preorder.js";

afterEach(() => vi.useRealTimers());

describe("предзаказы", () => {
  it("придержанные уходят в отдельную пачку и не мешают очереди", () => {
    const { live, parked } = splitPreorders([
      { id: 1, number: "31" },
      { id: 2, number: "7", preorder_held: true, is_preorder: true },
    ]);
    expect(live.map((o) => o.number)).toEqual(["31"]);
    expect(parked.map((o) => o.number)).toEqual(["7"]);
  });

  it("отложенные сортируются по близости срока", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-09T07:00:00Z"));
    const { parked } = splitPreorders([
      { id: 1, number: "поздний", preorder_held: true, complete_before: "2026-08-09T15:00:00Z" },
      { id: 2, number: "ближний", preorder_held: true, complete_before: "2026-08-09T09:00:00Z" },
    ]);
    expect(parked.map((o) => o.number)).toEqual(["ближний", "поздний"]);
  });

  it("срок подписан временем филиала", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-09T07:00:00Z"));
    const label = dueLabel({ complete_before: "2026-08-09T11:00:00Z" });
    expect(label).toMatch(/^к \d{2}:\d{2}$/);
  });

  it("без complete_before подписи нет, но заказ не ломается", () => {
    expect(dueLabel({})).toBeNull();
    expect(isPreorder({})).toBe(false);
  });
});
