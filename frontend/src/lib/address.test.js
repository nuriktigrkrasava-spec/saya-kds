import { describe, it, expect } from "vitest";
import { addressParts } from "./address.js";

// Строки взяты с прода: заказы операторов КЦ и заказы с сайта/приложения.
describe("addressParts", () => {
  it("разбирает наш формат из структурных полей iiko", () => {
    expect(addressParts("Кабанбай батыра, 59/3, кв. 110, подъезд 2, этаж 6")).toEqual({
      flat: "110", entrance: "2", floor: "6", doorphone: null,
    });
  });

  it("берёт домофон", () => {
    expect(
      addressParts("Мангилик Ел, 53, кв. 123, подъезд 5, этаж 5, домофон #4230").doorphone
    ).toBe("#4230");
  });

  it("понимает слитную запись с сайта", () => {
    expect(addressParts("г. Астана, ул. Кабанбай батыра 43в, кв.906,эт.9")).toEqual({
      flat: "906", entrance: null, floor: "9", doorphone: null,
    });
  });

  it("понимает числа перед словом", () => {
    expect(addressParts("г. Астана, ул. Е-779/32, 4 подъзд, 108 квартира")).toEqual({
      flat: "108", entrance: "4", floor: null, doorphone: null,
    });
  });

  it("понимает примечание к адресу словами", () => {
    expect(
      addressParts("г. Астана, ул. Туркестан 8, кв 155 (Примеч. к адресу: 5 подъезд, 4 этаж)")
    ).toEqual({ flat: "155", entrance: "5", floor: "4", doorphone: null });
  });

  it("понимает этаж через дефис", () => {
    expect(addressParts("ул. рыскулова 5/3, квартира 6 (этаж-3)")).toEqual({
      flat: "6", entrance: null, floor: "3", doorphone: null,
    });
  });

  it("понимает форму «кв./офис»", () => {
    expect(addressParts("ул. Кабанбай Батыр, д. 46, кв./офис 30").flat).toBe("30");
    expect(addressParts("ул. Жошы хан д. 23 кв./оф. 250 под. 5 эт. 7")).toEqual({
      flat: "250", entrance: "5", floor: "7", doorphone: null,
    });
  });

  it("не путает название улицы с «кв»", () => {
    expect(addressParts("ЖК Аквамарин, 12").flat).toBe(null);
  });

  it("не принимает номер дома за квартиру", () => {
    expect(addressParts("Мангилик Ел, 55В")).toEqual({
      flat: null, entrance: null, floor: null, doorphone: null,
    });
  });

  it("не падает на пустом адресе", () => {
    expect(addressParts("")).toEqual({
      flat: null, entrance: null, floor: null, doorphone: null,
    });
    expect(addressParts(null).flat).toBe(null);
  });
});
