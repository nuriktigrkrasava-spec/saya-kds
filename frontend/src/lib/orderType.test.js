import { describe, it, expect } from "vitest";
import { orderChannel } from "./orderType.js";

describe("orderChannel", () => {
  it("курьерская доставка требует нашего курьера", () => {
    const ch = orderChannel({ service_type: "DeliveryByCourier", customer: "Айгерим" });
    expect(ch.needsCourier).toBe(true);
  });

  it("агрегатор по имени клиента — без курьера", () => {
    expect(orderChannel({ customer: "Wolt", service_type: "DeliveryByCourier" }).needsCourier)
      .toBe(false);
  });

  it("агрегатор опознаётся и по типу оплаты", () => {
    // Прод 07.08.2026: заказ №91 «Яндекс безнал» приехал с типом
    // DeliveryByCourier и без «яндекса» в имени клиента — сборщику показали
    // выбор курьера, iiko назначение отбил. Тип оплаты у агрегаторов свой.
    const ch = orderChannel({
      customer: "Гость", service_type: "DeliveryByCourier",
      payment_type_name: "Яндекс безнал",
    });
    expect(ch.needsCourier).toBe(false);
    expect(ch.type).toBe("aggregator");
  });

  it("обычная карта не путается с агрегатором", () => {
    const ch = orderChannel({
      customer: "Гость", service_type: "DeliveryByCourier",
      payment_type_name: "КАСПИ онлайн",
    });
    expect(ch.needsCourier).toBe(true);
  });
});
