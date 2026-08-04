import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";
import AudioGuard from "./AudioGuard.jsx";
import { audioHealthy } from "../lib/sound.js";

// Модуль звука мокаем целиком: подменять экспорты ES-модуля через vi.spyOn
// нельзя — пространство имён модуля неизменяемо, и тест падает на "not extensible".
vi.mock("../lib/sound.js", () => ({
  audioHealthy: vi.fn(),
  unlockAudio: vi.fn(() => true),
}));

beforeEach(() => {
  vi.useFakeTimers();
  audioHealthy.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("AudioGuard", () => {
  it("показывает блокирующую плашку, когда звук мёртв", () => {
    audioHealthy.mockReturnValue(false);
    render(<AudioGuard />);
    expect(screen.getByText(/ЗВУК НЕ РАБОТАЕТ/i)).toBeTruthy();
  });

  it("ничего не рисует, когда звук жив", () => {
    audioHealthy.mockReturnValue(true);
    render(<AudioGuard />);
    expect(screen.queryByText(/ЗВУК НЕ РАБОТАЕТ/i)).toBe(null);
  });

  it("перепроверяет состояние каждые 5 секунд", () => {
    audioHealthy.mockReturnValue(true);
    render(<AudioGuard />);
    const before = audioHealthy.mock.calls.length;
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(audioHealthy.mock.calls.length).toBeGreaterThan(before);
  });
});
