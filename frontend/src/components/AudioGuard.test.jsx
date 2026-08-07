import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, act, fireEvent } from "@testing-library/react";
import AudioGuard from "./AudioGuard.jsx";
import { audioHealthy, unlockAudio } from "../lib/sound.js";

// Модуль звука мокаем целиком: подменять экспорты ES-модуля через vi.spyOn
// нельзя — пространство имён модуля неизменяемо, и тест падает на "not extensible".
vi.mock("../lib/sound.js", () => ({
  audioHealthy: vi.fn(),
  unlockAudio: vi.fn(() => true),
}));

// Шаг watchdog внутри компонента. Отдельная константа, чтобы «5 секунд» в тесте
// про интервал остались literal-ом из спеки, а прочие тесты не дублировали число.
const WATCHDOG_TICK_MS = 5000;

beforeEach(() => {
  vi.useFakeTimers();
  audioHealthy.mockReset();
  unlockAudio.mockClear();
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

  it("касание плашки разблокирует звук", () => {
    audioHealthy.mockReturnValue(false);
    render(<AudioGuard />);
    fireEvent.click(screen.getByRole("alert"));
    expect(unlockAudio).toHaveBeenCalled();
  });

  it("не прячет плашку, если после касания звук так и не ожил", () => {
    // Главная проверка задачи: скрытие завязано на реальное состояние контекста,
    // а не на факт нажатия. unlockAudio() возвращает true уже при создании
    // контекста, а resume() асинхронный — если поверить нажатию, экран
    // «замолчит молча»: плашки нет, звука нет.
    audioHealthy.mockReturnValue(false);
    render(<AudioGuard />);
    fireEvent.click(screen.getByRole("alert"));
    act(() => {
      vi.advanceTimersByTime(100); // перечитывание состояния после resume()
    });
    expect(screen.queryByText(/ЗВУК НЕ РАБОТАЕТ/i)).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(WATCHDOG_TICK_MS); // и следующий тик watchdog
    });
    expect(screen.queryByText(/ЗВУК НЕ РАБОТАЕТ/i)).toBeTruthy();
  });

  it("прячет плашку, когда после касания контекст дошёл до running", () => {
    audioHealthy.mockReturnValue(false);
    render(<AudioGuard />);
    fireEvent.click(screen.getByRole("alert"));
    audioHealthy.mockReturnValue(true); // resume() отработал
    act(() => {
      vi.advanceTimersByTime(100);
    });
    expect(screen.queryByText(/ЗВУК НЕ РАБОТАЕТ/i)).toBe(null);
  });

  it("сам поднимает плашку, если звук умер на работающем экране", () => {
    audioHealthy.mockReturnValue(true);
    render(<AudioGuard />);
    expect(screen.queryByText(/ЗВУК НЕ РАБОТАЕТ/i)).toBe(null);
    audioHealthy.mockReturnValue(false); // планшет уснул, контекст приостановлен
    act(() => {
      vi.advanceTimersByTime(WATCHDOG_TICK_MS);
    });
    expect(screen.getByText(/ЗВУК НЕ РАБОТАЕТ/i)).toBeTruthy();
  });
});
