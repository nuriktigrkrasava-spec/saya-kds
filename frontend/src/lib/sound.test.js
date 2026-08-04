import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  migrateLegacySoundPref,
  unlockAudio,
  audioHealthy,
  playNewOrder,
} from "./sound.js";

// Мок AudioContext: state управляется вручную, узлы — заглушки.
class FakeAudioContext {
  constructor() {
    this.state = "suspended";
    this.currentTime = 0;
    this.destination = {};
    FakeAudioContext.last = this;
  }
  resume() {
    this.state = "running";
    return Promise.resolve();
  }
  createOscillator() {
    return {
      type: "sine",
      frequency: { setValueAtTime() {} },
      connect: (n) => n,
      start() {},
      stop() {},
    };
  }
  createGain() {
    return {
      gain: { setValueAtTime() {}, exponentialRampToValueAtTime() {} },
      connect: (n) => n,
    };
  }
}

beforeEach(() => {
  localStorage.clear();
  FakeAudioContext.last = null;
  globalThis.AudioContext = FakeAudioContext;
  window.AudioContext = FakeAudioContext;
});

describe("миграция старой настройки звука", () => {
  it("стирает ключ saya_sound_on, чтобы ранее заглушенный планшет заговорил", () => {
    localStorage.setItem("saya_sound_on", "0");
    migrateLegacySoundPref();
    expect(localStorage.getItem("saya_sound_on")).toBe(null);
  });

  it("не падает, если ключа не было", () => {
    expect(() => migrateLegacySoundPref()).not.toThrow();
  });
});

describe("модуль звука не даёт себя выключить", () => {
  it("не экспортирует переключателей звука", async () => {
    const mod = await import("./sound.js");
    expect(mod.isSoundOn).toBeUndefined();
    expect(mod.setSoundOn).toBeUndefined();
  });

  it("audioHealthy=false до разблокировки и true после", async () => {
    expect(audioHealthy()).toBe(false);
    expect(unlockAudio()).toBe(true);
    await Promise.resolve();
    expect(audioHealthy()).toBe(true);
  });

  it("играет без всякой настройки — достаточно живого контекста", async () => {
    unlockAudio();
    await Promise.resolve();
    // Контекст в модуле общий и переживает предыдущие тесты, поэтому
    // FakeAudioContext.last может быть пустым — следим за прототипом.
    const spy = vi.spyOn(FakeAudioContext.prototype, "createOscillator");
    playNewOrder();
    expect(spy).toHaveBeenCalled();
  });
});
