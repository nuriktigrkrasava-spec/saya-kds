import { describe, it, expect } from "vitest";
import { orderChannel, completeLabel, needsPaymentChoice } from "./orderType.js";

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

describe("вынос с кассы", () => {
  it("ToGo — это вынос, а не самовывоз", () => {
    const ch = orderChannel({ service_type: "ToGo" });
    expect(ch.type).toBe("togo");
    expect(ch.label).toBe("Вынос");
    expect(ch.needsCourier).toBe(false);
  });

  it("заказ с сайта остаётся самовывозом", () => {
    expect(orderChannel({ service_type: "DeliveryByClient" }).label).toBe("Самовывоз");
  });

  it("кнопка называет действие", () => {
    expect(completeLabel(orderChannel({ service_type: "ToGo" }))).toBe("Выдан на вынос");
  });
});

describe("выбор типа оплаты", () => {
  it("зал и вынос закрываем мы — спрашиваем оплату", () => {
    expect(needsPaymentChoice(orderChannel({ service_type: "Common" }))).toBe(true);
    expect(needsPaymentChoice(orderChannel({ service_type: "ToGo" }))).toBe(true);
  });

  it("самовывоз с сайта и агрегаторы оплачены заранее — не спрашиваем", () => {
    expect(needsPaymentChoice(orderChannel({ service_type: "DeliveryByClient" }))).toBe(false);
    expect(needsPaymentChoice(orderChannel({ customer: "Wolt" }))).toBe(false);
  });

  it("у доставки оплату берёт курьер, а не сборщик", () => {
    expect(needsPaymentChoice(orderChannel({ service_type: "DeliveryByCourier" }))).toBe(false);
  });
});
