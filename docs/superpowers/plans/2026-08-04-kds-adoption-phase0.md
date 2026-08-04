# KDS Adoption Фаза 0 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Сделать звук о новом заказе технически неотключаемым и начать измерять, пользуется ли персонал KDS вообще.

**Architecture:** Фронтенд теряет возможность заглушить звук (тоггл удаляется, watchdog блокирует экран при мёртвом `AudioContext`, Wake Lock держит планшет включённым) и начинает слать хартбит о своём состоянии. Бэкенд принимает хартбиты в новую таблицу `ScreenHeartbeat` и считает покрытие KDS по уже существующим `OrderEvent` — по доле позиций, получивших отметку через экран. Новые ручки живут в отдельных роутерах, а не в разросшемся `api.py`.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 (async) + SQLite/Postgres, pytest + pytest-asyncio; React 18 + Vite, vitest + jsdom (добавляются в Task 1).

## Global Constraints

- Кухонный экран — **планшет Android в браузере**. Всё решение обязано работать без нативного приложения.
- **Понятия «выключить звук» не существует.** Нельзя вводить настройку, флаг или кнопку, глушащую звук.
- Питон: `requires-python = ">=3.12"`, pytest работает в `asyncio_mode = "auto"` (async-тесты без декоратора).
- Новые бэкенд-ручки **не добавляются в `backend/app/api.py`** (2062 строки) — только отдельными `APIRouter` в своих модулях.
- Все новые ручки живут под префиксом `/api/`.
- Часовой пояс филиала берётся из `settings.iiko_tz_offset_hours` (по умолчанию 5), не хардкодится.
- Один филиал: везде фильтруем по `org_id`.
- Язык комментариев в коде — русский, как в существующих модулях.

---

### Task 1: Vitest + звук без возможности выключения

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/vite.config.js`
- Modify: `frontend/src/lib/sound.js:9-29` (удалить `STORAGE_KEY`, `isSoundOn`, `setSoundOn`)
- Test: `frontend/src/lib/sound.test.js`

**Interfaces:**
- Consumes: ничего.
- Produces:
  - `migrateLegacySoundPref(): void` — удаляет ключ `saya_sound_on` из localStorage.
  - `unlockAudio(): boolean` — создаёт/возобновляет `AudioContext`, возвращает `true` при успехе.
  - `audioHealthy(): boolean` — `true`, только если контекст создан и `state === "running"`.
  - `playNewOrder(): void`, `playOverdue(): void`, `playHandoff(): void` — без проверки настройки, играют всегда при живом контексте.
  - Экспортов `isSoundOn` / `setSoundOn` больше НЕТ.

- [ ] **Step 1: Установить vitest и jsdom**

```bash
cd frontend && npm install -D vitest@^2.1.0 jsdom@^25.0.0
```

- [ ] **Step 2: Включить тестовый раннер**

В `frontend/vite.config.js` добавить секцию `test` в объект конфига:

```js
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
  },
  test: {
    environment: "jsdom",
    globals: true,
  },
});
```

В `frontend/package.json` в `"scripts"` добавить строку (остальные скрипты не трогать):

```json
    "test": "vitest run",
```

- [ ] **Step 3: Написать падающий тест**

Создать `frontend/src/lib/sound.test.js`:

```js
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
    const spy = vi.spyOn(FakeAudioContext.last, "createOscillator");
    playNewOrder();
    expect(spy).toHaveBeenCalled();
  });
});
```

- [ ] **Step 4: Запустить тест и убедиться, что он падает**

Run: `cd frontend && npm test`
Expected: FAIL — `migrateLegacySoundPref is not a function` и `audioHealthy is not a function`.

- [ ] **Step 5: Переписать шапку sound.js**

В `frontend/src/lib/sound.js` **удалить целиком** блок с `STORAGE_KEY`, `isSoundOn` и `setSoundOn` (строки 9-29) и заменить его на:

```js
// Ключ старой настройки звука. Больше не читается: звук выключить нельзя.
// Стираем его при загрузке, иначе планшеты, где звук когда-то выключили,
// остались бы немыми навсегда.
const LEGACY_KEY = "saya_sound_on";

let ctx = null; // общий AudioContext (создаётся при первом unlockAudio)

/** Одноразовая зачистка старой настройки. Вызывается при старте приложения. */
export function migrateLegacySoundPref() {
  try {
    localStorage.removeItem(LEGACY_KEY);
  } catch {
    /* приватный режим — игнорируем */
  }
}

/** Контекст жив и реально играет? На этом построен watchdog в AudioGuard. */
export function audioHealthy() {
  return !!ctx && ctx.state === "running";
}
```

- [ ] **Step 6: Убрать проверку настройки из всех трёх сигналов**

В том же файле в `playNewOrder`, `playOverdue` и `playHandoff` заменить первую строку тела
`if (!isSoundOn() || !ready()) return;` на:

```js
  if (!ready()) return;
```

(во всех трёх функциях; функция `ready()` остаётся как есть).

- [ ] **Step 7: Запустить тесты и убедиться, что проходят**

Run: `cd frontend && npm test`
Expected: PASS, 5 тестов.

- [ ] **Step 8: Коммит**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vite.config.js frontend/src/lib/sound.js frontend/src/lib/sound.test.js
git commit -m "feat(sound): звук нельзя выключить — убран тоггл, добавлен audioHealthy"
```

---

### Task 2: AudioGuard — экран не может работать молча

**Files:**
- Create: `frontend/src/components/AudioGuard.jsx`
- Modify: `frontend/src/components/ScreenHeader.jsx:1-75` (убрать колокольчик)
- Modify: `frontend/src/components/KitchenScreen.jsx:145-150` (смонтировать AudioGuard)
- Modify: `frontend/src/screens/Login.jsx` (разблокировка звука на входе)
- Modify: `frontend/src/index.css` (стили плашки)
- Test: `frontend/src/components/AudioGuard.test.jsx`

**Interfaces:**
- Consumes: `audioHealthy()`, `unlockAudio()` из `lib/sound.js` (Task 1).
- Produces: React-компонент `<AudioGuard />` без пропсов. Сам опрашивает `audioHealthy()` раз в 5 секунд и рисует блокирующий оверлей, пока звук мёртв.

- [ ] **Step 1: Установить библиотеку для тестов компонентов**

```bash
cd frontend && npm install -D @testing-library/react@^16.0.0 @testing-library/dom@^10.4.0
```

- [ ] **Step 2: Написать падающий тест**

Создать `frontend/src/components/AudioGuard.test.jsx`:

```jsx
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
```

- [ ] **Step 3: Запустить тест и убедиться, что он падает**

Run: `cd frontend && npm test`
Expected: FAIL — `Failed to resolve import "./AudioGuard.jsx"`.

- [ ] **Step 4: Создать компонент**

Создать `frontend/src/components/AudioGuard.jsx`:

```jsx
import { useEffect, useState } from "react";
import { audioHealthy, unlockAudio } from "../lib/sound.js";

// Как часто проверяем, жив ли звук. Планшет мог уснуть, браузер — приостановить
// AudioContext, повар — перезагрузить вкладку. Всё это чинится одним касанием.
const WATCHDOG_MS = 5000;

/**
 * Полноэкранная блокирующая плашка на случай мёртвого звука.
 *
 * Смысл: экран физически не может работать молча. Пока AudioContext не в
 * состоянии running, заказы под плашкой не видны и не нажимаются — единственное
 * доступное действие это коснуться экрана, что и разблокирует звук.
 */
export default function AudioGuard() {
  const [ok, setOk] = useState(() => audioHealthy());

  useEffect(() => {
    const id = setInterval(() => setOk(audioHealthy()), WATCHDOG_MS);
    return () => clearInterval(id);
  }, []);

  function fix() {
    unlockAudio();
    // resume() асинхронный — перечитываем состояние следующим тиком.
    setTimeout(() => setOk(audioHealthy()), 100);
  }

  if (ok) return null;

  return (
    <div className="audio-guard" onClick={fix} role="alert">
      <div className="audio-guard__icon" aria-hidden="true">🔇</div>
      <div className="audio-guard__text">ЗВУК НЕ РАБОТАЕТ</div>
      <div className="audio-guard__hint">КОСНИСЬ ЭКРАНА</div>
    </div>
  );
}
```

- [ ] **Step 5: Добавить стили**

В конец `frontend/src/index.css` добавить:

```css
/* Блокирующая плашка мёртвого звука. Перекрывает всё: работать молча нельзя. */
.audio-guard {
  position: fixed;
  inset: 0;
  z-index: 9999;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  background: #c0392b;
  color: #fff;
  font-weight: 800;
  text-align: center;
  cursor: pointer;
  animation: audio-guard-pulse 1s ease-in-out infinite;
}
.audio-guard__icon { font-size: 96px; line-height: 1; }
.audio-guard__text { font-size: 44px; letter-spacing: 1px; }
.audio-guard__hint { font-size: 28px; opacity: 0.9; }
@keyframes audio-guard-pulse {
  0%, 100% { background: #c0392b; }
  50% { background: #e74c3c; }
}
```

- [ ] **Step 6: Смонтировать AudioGuard на кухонном экране**

В `frontend/src/components/KitchenScreen.jsx` добавить импорт рядом с остальными импортами компонентов (после строки 4):

```jsx
import AudioGuard from "./AudioGuard.jsx";
```

И вставить компонент первой строкой внутри `<div className="screen" ...>` (перед `<ScreenHeader ... />`):

```jsx
      <AudioGuard />
```

- [ ] **Step 7: Убрать колокольчик из шапки**

В `frontend/src/components/ScreenHeader.jsx`:

Заменить строку 4 (импорт звука) — удалить её целиком:

```jsx
import { isSoundOn, setSoundOn, unlockAudio, playNewOrder } from "../lib/sound.js";
```

Удалить строки 13-28 — состояние `soundOn` и функцию `toggleSound`:

```jsx
  // Bell toggle. Starts from the persisted preference.
  const [soundOn, setSound] = useState(() => isSoundOn());

  function toggleSound() {
    ...
  }
```

Удалить сам `<button className={`bell-btn ...`}>...</button>` целиком (строки 53-66).

Убрать теперь неиспользуемый импорт `useState` в строке 1, если он больше нигде в файле не нужен (после удаления `soundOn` он не нужен) — строка 1 становится:

```jsx
import { Link, useNavigate } from "react-router-dom";
```

- [ ] **Step 8: Разблокировать звук на входе оператора**

В `frontend/src/screens/Login.jsx` добавить импорт:

```jsx
import { unlockAudio, migrateLegacySoundPref } from "../lib/sound.js";
```

и вызвать обе функции внутри обработчика успешного входа, сразу после сохранения сессии (это происходит внутри клика — единственный момент, когда браузер разрешает создать AudioContext):

```jsx
    migrateLegacySoundPref();
    unlockAudio();
```

- [ ] **Step 9: Запустить тесты**

Run: `cd frontend && npm test`
Expected: PASS, 8 тестов (5 из Task 1 + 3 новых).

- [ ] **Step 10: Проверить сборку**

Run: `cd frontend && npm run build`
Expected: сборка проходит без ошибок — значит, удалённые импорты нигде не остались.

- [ ] **Step 11: Коммит**

```bash
git add frontend/src/components/AudioGuard.jsx frontend/src/components/AudioGuard.test.jsx frontend/src/components/ScreenHeader.jsx frontend/src/components/KitchenScreen.jsx frontend/src/screens/Login.jsx frontend/src/index.css frontend/package.json frontend/package-lock.json
git commit -m "feat(sound): AudioGuard блокирует экран при мёртвом звуке, колокольчик удалён"
```

---

### Task 3: Wake Lock — планшет не гаснет

**Files:**
- Create: `frontend/src/lib/wakelock.js`
- Modify: `frontend/src/components/KitchenScreen.jsx` (вызов хука)
- Test: `frontend/src/lib/wakelock.test.js`

**Interfaces:**
- Consumes: ничего.
- Produces:
  - `useWakeLock(): { supported: boolean, active: boolean }` — React-хук. Берёт `screen` wake lock на монтировании, переподнимает его при возврате вкладки в видимость, отпускает при размонтировании.

- [ ] **Step 1: Написать падающий тест**

Создать `frontend/src/lib/wakelock.test.js`:

```js
import { describe, it, expect, beforeEach, vi } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useWakeLock } from "./wakelock.js";

function installWakeLock() {
  const sentinel = { released: false, release: vi.fn(async () => {}) };
  const request = vi.fn(async () => sentinel);
  Object.defineProperty(navigator, "wakeLock", {
    value: { request },
    configurable: true,
    writable: true,
  });
  return { request, sentinel };
}

beforeEach(() => {
  delete navigator.wakeLock;
});

describe("useWakeLock", () => {
  it("сообщает supported=false, когда API нет", () => {
    const { result } = renderHook(() => useWakeLock());
    expect(result.current.supported).toBe(false);
  });

  it("берёт лок на монтировании", async () => {
    const { request } = installWakeLock();
    const { result } = renderHook(() => useWakeLock());
    expect(result.current.supported).toBe(true);
    await waitFor(() => expect(request).toHaveBeenCalledWith("screen"));
  });

  it("переподнимает лок, когда вкладка снова видима", async () => {
    const { request } = installWakeLock();
    renderHook(() => useWakeLock());
    await waitFor(() => expect(request).toHaveBeenCalledTimes(1));

    Object.defineProperty(document, "visibilityState", {
      value: "visible",
      configurable: true,
    });
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
  });
});
```

- [ ] **Step 2: Запустить тест и убедиться, что он падает**

Run: `cd frontend && npm test src/lib/wakelock.test.js`
Expected: FAIL — `Failed to resolve import "./wakelock.js"`.

- [ ] **Step 3: Реализовать хук**

Создать `frontend/src/lib/wakelock.js`:

```js
import { useEffect, useState } from "react";

/**
 * Держит экран планшета включённым, пока открыт кухонный экран.
 *
 * Зачем: KDS живёт в браузере на Android-планшете. Погасший экран не только не
 * виден повару — браузер начинает троттлить таймеры вкладки, и наши секундные
 * тики срочности останавливаются.
 *
 * Лок теряется при любом уходе вкладки в фон, поэтому переподнимаем его на
 * visibilitychange. Если API не поддержан — работаем без него, вернув
 * supported=false (это уходит в хартбит, чтобы отсутствие лока было видно).
 */
export function useWakeLock() {
  const supported =
    typeof navigator !== "undefined" && "wakeLock" in navigator;
  const [active, setActive] = useState(false);

  useEffect(() => {
    if (!supported) return undefined;

    let sentinel = null;
    let alive = true;

    async function acquire() {
      try {
        sentinel = await navigator.wakeLock.request("screen");
        if (!alive) {
          await sentinel.release();
          return;
        }
        setActive(true);
        sentinel.addEventListener?.("release", () => setActive(false));
      } catch {
        // Отказ (батарея, политика браузера) — не повод падать.
        setActive(false);
      }
    }

    function onVisible() {
      if (document.visibilityState === "visible") acquire();
    }

    acquire();
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      alive = false;
      document.removeEventListener("visibilitychange", onVisible);
      sentinel?.release?.().catch(() => {});
    };
  }, [supported]);

  return { supported, active };
}
```

- [ ] **Step 4: Запустить тест и убедиться, что проходит**

Run: `cd frontend && npm test src/lib/wakelock.test.js`
Expected: PASS, 3 теста.

- [ ] **Step 5: Подключить на кухонном экране**

В `frontend/src/components/KitchenScreen.jsx` добавить импорт:

```jsx
import { useWakeLock } from "../lib/wakelock.js";
```

и вызвать хук сразу после `useTick(1000);` (строка 49):

```jsx
  useWakeLock(); // планшет не должен гаснуть посреди смены
```

- [ ] **Step 6: Прогнать весь фронтовый набор**

Run: `cd frontend && npm test`
Expected: PASS, 11 тестов.

- [ ] **Step 7: Коммит**

```bash
git add frontend/src/lib/wakelock.js frontend/src/lib/wakelock.test.js frontend/src/components/KitchenScreen.jsx
git commit -m "feat(kds): wake lock — экран планшета не гаснет за смену"
```

---

### Task 4: Хартбит экранов — бэкенд

**Files:**
- Modify: `backend/app/models.py` (добавить модель `ScreenHeartbeat` в конец файла)
- Create: `backend/app/heartbeat.py`
- Modify: `backend/app/main.py:11-17` (подключить роутер), `backend/app/main.py:127-130` (`_API_PREFIXES`)
- Test: `backend/tests/test_heartbeat.py`

**Interfaces:**
- Consumes: `app.db.get_session`, `app.models.Base`.
- Produces:
  - Таблица `screen_heartbeats` с колонками `screen_id` (уникальная), `station`, `org_id`, `operator_code`, `operator_name`, `audio_ok`, `wake_lock_ok`, `last_seen`.
  - `POST /api/screens/heartbeat` — тело `{screen_id, station, org_id, operator_code, operator_name, audio_ok, wake_lock_ok}`, ответ `{"ok": true}`. Upsert по `screen_id`.
  - `GET /api/screens?org_id=...` — список `[{screen_id, station, operator_name, audio_ok, wake_lock_ok, last_seen, online}]`, где `online = (utcnow - last_seen) < 90 сек`.
  - `router: APIRouter` в модуле `app.heartbeat`.

- [ ] **Step 1: Написать падающий тест**

Создать `backend/tests/test_heartbeat.py`:

```python
from datetime import datetime, timedelta

import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

from app.api import app as fastapi_app
from app.db import Base, get_session
from app.heartbeat import router as heartbeat_router
import app.models  # noqa: F401  (регистрирует таблицы)
from app.models import ScreenHeartbeat


@pytest_asyncio.fixture
async def client():
    """FastAPI-приложение с роутером хартбитов поверх in-memory SQLite."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def override_session():
        async with maker() as s:
            yield s

    if not any(getattr(r, "path", "") == "/api/screens/heartbeat"
               for r in fastapi_app.routes):
        fastapi_app.include_router(heartbeat_router)
    fastapi_app.dependency_overrides[get_session] = override_session

    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.maker = maker
        yield c

    fastapi_app.dependency_overrides.clear()
    await engine.dispose()


async def test_heartbeat_creates_row(client):
    """Первый хартбит создаёт запись об экране."""
    resp = await client.post("/api/screens/heartbeat", json={
        "screen_id": "s-1", "station": "rolls", "org_id": "o1",
        "operator_code": "E1", "operator_name": "Азер",
        "audio_ok": True, "wake_lock_ok": True,
    })
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    async with client.maker() as s:
        rows = (await s.execute(select(ScreenHeartbeat))).scalars().all()
    assert len(rows) == 1
    assert rows[0].screen_id == "s-1"
    assert rows[0].operator_name == "Азер"
    assert rows[0].audio_ok is True


async def test_heartbeat_upserts_not_duplicates(client):
    """Повторный хартбит того же экрана обновляет строку, а не плодит новые."""
    body = {
        "screen_id": "s-1", "station": "rolls", "org_id": "o1",
        "operator_code": "E1", "operator_name": "Азер",
        "audio_ok": True, "wake_lock_ok": True,
    }
    await client.post("/api/screens/heartbeat", json=body)
    await client.post("/api/screens/heartbeat", json={**body, "audio_ok": False})

    async with client.maker() as s:
        rows = (await s.execute(select(ScreenHeartbeat))).scalars().all()
    assert len(rows) == 1
    assert rows[0].audio_ok is False


async def test_screens_list_marks_stale_as_offline(client):
    """Экран, не писавший больше 90 секунд, считается офлайном."""
    async with client.maker() as s:
        s.add(ScreenHeartbeat(
            screen_id="fresh", station="rolls", org_id="o1",
            operator_name="Азер", audio_ok=True, wake_lock_ok=True,
            last_seen=datetime.utcnow(),
        ))
        s.add(ScreenHeartbeat(
            screen_id="stale", station="pizza", org_id="o1",
            operator_name="Мурад", audio_ok=True, wake_lock_ok=True,
            last_seen=datetime.utcnow() - timedelta(minutes=10),
        ))
        await s.commit()

    resp = await client.get("/api/screens?org_id=o1")
    assert resp.status_code == 200
    by_id = {r["screen_id"]: r for r in resp.json()}
    assert by_id["fresh"]["online"] is True
    assert by_id["stale"]["online"] is False


async def test_screens_list_filters_by_org(client):
    """Чужой филиал не виден."""
    async with client.maker() as s:
        s.add(ScreenHeartbeat(
            screen_id="mine", station="rolls", org_id="o1",
            last_seen=datetime.utcnow(),
        ))
        s.add(ScreenHeartbeat(
            screen_id="theirs", station="rolls", org_id="o2",
            last_seen=datetime.utcnow(),
        ))
        await s.commit()

    resp = await client.get("/api/screens?org_id=o1")
    ids = [r["screen_id"] for r in resp.json()]
    assert ids == ["mine"]
```

- [ ] **Step 2: Запустить тест и убедиться, что он падает**

Run: `cd backend && python -m pytest tests/test_heartbeat.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.heartbeat'`.

- [ ] **Step 3: Добавить модель**

В конец `backend/app/models.py` добавить:

```python
class ScreenHeartbeat(Base):
    """Живой пульс кухонного экрана.

    Отвечает на вопрос, который иначе задать невозможно: экран вообще был
    открыт? Без этого «позиция не отмечена» неотличимо от «планшет лежал
    выключенным», а значит и разговаривать с поваром не о чем."""
    __tablename__ = "screen_heartbeats"
    id: Mapped[int] = mapped_column(primary_key=True)
    screen_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    station: Mapped[str] = mapped_column(String, default="")
    org_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    operator_code: Mapped[str | None] = mapped_column(String, nullable=True)
    operator_name: Mapped[str | None] = mapped_column(String, nullable=True)
    audio_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    wake_lock_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
```

- [ ] **Step 4: Создать роутер**

Создать `backend/app/heartbeat.py`:

```python
from __future__ import annotations
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import ScreenHeartbeat

router = APIRouter(prefix="/api/screens", tags=["screens"])

# Экран шлёт пульс раз в 30с. Три пропущенных подряд — считаем, что он погас.
ONLINE_WINDOW_SECONDS = 90


class HeartbeatIn(BaseModel):
    screen_id: str
    station: str = ""
    org_id: str | None = None
    operator_code: str | None = None
    operator_name: str | None = None
    audio_ok: bool = False
    wake_lock_ok: bool = False


@router.post("/heartbeat")
async def heartbeat(body: HeartbeatIn,
                    session: AsyncSession = Depends(get_session)) -> dict:
    """Принимает пульс экрана. Upsert по screen_id: экранов мало, строк ровно
    столько, сколько планшетов, история здесь не нужна."""
    row = (await session.execute(
        select(ScreenHeartbeat).where(
            ScreenHeartbeat.screen_id == body.screen_id)
    )).scalar_one_or_none()

    if row is None:
        row = ScreenHeartbeat(screen_id=body.screen_id)
        session.add(row)

    row.station = body.station
    row.org_id = body.org_id
    row.operator_code = body.operator_code
    row.operator_name = body.operator_name
    row.audio_ok = body.audio_ok
    row.wake_lock_ok = body.wake_lock_ok
    row.last_seen = datetime.utcnow()

    await session.commit()
    return {"ok": True}


@router.get("")
async def list_screens(org_id: str,
                       session: AsyncSession = Depends(get_session)) -> list[dict]:
    """Состояние экранов филиала: кто за ними, играет ли звук, живы ли они."""
    rows = (await session.execute(
        select(ScreenHeartbeat)
        .where(ScreenHeartbeat.org_id == org_id)
        .order_by(ScreenHeartbeat.screen_id)
    )).scalars().all()

    cutoff = datetime.utcnow() - timedelta(seconds=ONLINE_WINDOW_SECONDS)
    return [{
        "screen_id": r.screen_id,
        "station": r.station,
        "operator_code": r.operator_code,
        "operator_name": r.operator_name,
        "audio_ok": r.audio_ok,
        "wake_lock_ok": r.wake_lock_ok,
        "last_seen": r.last_seen.isoformat() if r.last_seen else None,
        "online": bool(r.last_seen and r.last_seen >= cutoff),
    } for r in rows]
```

- [ ] **Step 5: Подключить роутер и защитить префикс от SPA**

В `backend/app/main.py` после строки 17 (`import app.models  # noqa: F401`) добавить:

```python
from app.heartbeat import router as heartbeat_router

fastapi_app.include_router(heartbeat_router)
```

В том же файле в кортеж `_API_PREFIXES` (строки 127-130) добавить `"api"` первым элементом, чтобы неизвестный путь под `/api/` отдавал 404, а не `index.html`:

```python
_API_PREFIXES = (
    "api",
    "health", "orders", "items", "webhooks", "debug", "auth",
    "rms", "assets", "docs", "openapi.json", "redoc",
)
```

- [ ] **Step 6: Запустить тесты и убедиться, что проходят**

Run: `cd backend && python -m pytest tests/test_heartbeat.py -v`
Expected: PASS, 4 теста.

- [ ] **Step 7: Убедиться, что ничего не сломано**

Run: `cd backend && python -m pytest -q`
Expected: весь набор зелёный.

- [ ] **Step 8: Коммит**

```bash
git add backend/app/models.py backend/app/heartbeat.py backend/app/main.py backend/tests/test_heartbeat.py
git commit -m "feat(heartbeat): пульс кухонных экранов — видно, был ли экран открыт"
```

---

### Task 5: Отправка хартбита с экрана

**Files:**
- Create: `frontend/src/lib/heartbeat.js`
- Modify: `frontend/src/lib/api.js:84-152` (добавить вызов в объект `api`)
- Modify: `frontend/src/components/KitchenScreen.jsx` (вызов хука)
- Test: `frontend/src/lib/heartbeat.test.js`

**Interfaces:**
- Consumes: `audioHealthy()` из `lib/sound.js` (Task 1), `useWakeLock()` из `lib/wakelock.js` (Task 3), `POST /api/screens/heartbeat` (Task 4), `getSession()` из `lib/auth.js`.
- Produces:
  - `getScreenId(): string` — стабильный идентификатор планшета, живёт в `localStorage` под ключом `saya_screen_id`.
  - `useHeartbeat(station: string, wakeLockActive: boolean): void` — React-хук, шлющий пульс раз в 30 секунд.

- [ ] **Step 1: Написать падающий тест**

Создать `frontend/src/lib/heartbeat.test.js`:

```js
import { describe, it, expect, beforeEach } from "vitest";
import { getScreenId } from "./heartbeat.js";

beforeEach(() => {
  localStorage.clear();
});

describe("getScreenId", () => {
  it("генерирует идентификатор и сохраняет его", () => {
    const id = getScreenId();
    expect(typeof id).toBe("string");
    expect(id.length).toBeGreaterThan(0);
    expect(localStorage.getItem("saya_screen_id")).toBe(id);
  });

  it("возвращает тот же идентификатор при повторных вызовах", () => {
    expect(getScreenId()).toBe(getScreenId());
  });

  it("уважает уже сохранённый идентификатор", () => {
    localStorage.setItem("saya_screen_id", "planshet-rolls");
    expect(getScreenId()).toBe("planshet-rolls");
  });
});
```

- [ ] **Step 2: Запустить тест и убедиться, что он падает**

Run: `cd frontend && npm test src/lib/heartbeat.test.js`
Expected: FAIL — `Failed to resolve import "./heartbeat.js"`.

- [ ] **Step 3: Добавить вызов в api.js**

В `frontend/src/lib/api.js` в объект `api` (после строки `getOrders: ...`) добавить:

```js
  screenHeartbeat: (body) => post("/api/screens/heartbeat", body),
  screens: (orgId) => get(`/api/screens?org_id=${encodeURIComponent(orgId)}`),
```

- [ ] **Step 4: Создать модуль**

Создать `frontend/src/lib/heartbeat.js`:

```js
import { useEffect, useRef } from "react";
import { api } from "./api.js";
import { getSession } from "./auth.js";
import { audioHealthy } from "./sound.js";

const SCREEN_KEY = "saya_screen_id";

// Пульс раз в 30с; сервер считает экран офлайном после 90с молчания.
const BEAT_MS = 30000;

/**
 * Стабильный идентификатор планшета. Именно планшета, а не сотрудника: за одним
 * экраном за смену меняются люди, а вопрос «был ли этот экран включён» задаётся
 * к железу.
 */
export function getScreenId() {
  try {
    const saved = localStorage.getItem(SCREEN_KEY);
    if (saved) return saved;
    const id =
      globalThis.crypto?.randomUUID?.() ??
      `s-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    localStorage.setItem(SCREEN_KEY, id);
    return id;
  } catch {
    // Приватный режим: идентификатор не переживёт перезагрузку, но пульс идёт.
    return `s-${Date.now()}`;
  }
}

/**
 * Шлёт пульс экрана раз в 30 секунд.
 * @param {string} station    станция экрана ("rolls" / "pizza")
 * @param {boolean} wakeLockActive  держится ли wake lock прямо сейчас
 */
export function useHeartbeat(station, wakeLockActive) {
  // Держим свежие значения в ref, чтобы интервал не пересоздавался на каждый
  // тик рендера (KitchenScreen перерисовывается раз в секунду).
  const stateRef = useRef({ station, wakeLockActive });
  stateRef.current = { station, wakeLockActive };

  useEffect(() => {
    let alive = true;

    async function beat() {
      if (!alive) return;
      const session = getSession();
      try {
        await api.screenHeartbeat({
          screen_id: getScreenId(),
          station: stateRef.current.station || "",
          org_id: (session && session.org_id) || null,
          operator_code: (session && session.branch_code) || null,
          operator_name: (session && session.name) || null,
          audio_ok: audioHealthy(),
          wake_lock_ok: !!stateRef.current.wakeLockActive,
        });
      } catch {
        // Нет связи — пропускаем такт. Пропуск сам по себе информация:
        // сервер увидит дырку в пульсе.
      }
    }

    beat();
    const id = setInterval(beat, BEAT_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);
}
```

- [ ] **Step 5: Запустить тест и убедиться, что проходит**

Run: `cd frontend && npm test src/lib/heartbeat.test.js`
Expected: PASS, 3 теста.

- [ ] **Step 6: Подключить на кухонном экране**

В `frontend/src/components/KitchenScreen.jsx` добавить импорт:

```jsx
import { useHeartbeat } from "../lib/heartbeat.js";
```

и заменить строку с `useWakeLock();` (добавлена в Task 3) на:

```jsx
  const wake = useWakeLock(); // планшет не должен гаснуть посреди смены
  useHeartbeat(stations?.[0] || title, wake.active);
```

- [ ] **Step 7: Прогнать весь фронтовый набор и сборку**

Run: `cd frontend && npm test && npm run build`
Expected: PASS, 14 тестов; сборка проходит.

- [ ] **Step 8: Коммит**

```bash
git add frontend/src/lib/heartbeat.js frontend/src/lib/heartbeat.test.js frontend/src/lib/api.js frontend/src/components/KitchenScreen.jsx
git commit -m "feat(heartbeat): кухонный экран шлёт пульс со здоровьем звука и wake lock"
```

---

### Task 6: Метрика покрытия KDS — бэкенд

**Files:**
- Create: `backend/app/adoption.py`
- Modify: `backend/app/main.py` (подключить роутер рядом с `heartbeat_router`)
- Test: `backend/tests/test_adoption.py`

**Interfaces:**
- Consumes: `app.models.Order`, `app.models.OrderItem`, `app.models.OrderEvent`, `app.db.get_session`, `app.config.settings.iiko_tz_offset_hours`.
- Produces:
  - `GET /api/analytics/adoption?org_id=...&from=YYYY-MM-DD&to=YYYY-MM-DD` → объект:
    `{org_id, from, to, total_items, marked_items, coverage_pct, by_person: [{actor_name, marked}], by_day: [{date, total, marked, coverage_pct}], by_station: [{station, total, marked, coverage_pct}]}`.
  - `router: APIRouter` в модуле `app.adoption`.

**Определение покрытия.** Знаменатель — позиции заказов, которые уже дошли до
конца (статус `Closed` или проставлен `delivered_at`): их точно готовили.
Числитель — те из них, у которых есть `OrderEvent` типа `item_ready` с непустым
`actor_name`, то есть кто-то живой нажал «Готово» на экране. Разница между ними
и есть еда, приготовленная мимо KDS.

- [ ] **Step 1: Написать падающий тест**

Создать `backend/tests/test_adoption.py`:

```python
from datetime import datetime, timedelta

import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

from app.adoption import router as adoption_router
from app.api import app as fastapi_app
from app.db import Base, get_session
import app.models  # noqa: F401  (регистрирует таблицы)
from app.models import Order, OrderItem, OrderEvent


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def override_session():
        async with maker() as s:
            yield s

    if not any(getattr(r, "path", "") == "/api/analytics/adoption"
               for r in fastapi_app.routes):
        fastapi_app.include_router(adoption_router)
    fastapi_app.dependency_overrides[get_session] = override_session

    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.maker = maker
        yield c

    fastapi_app.dependency_overrides.clear()
    await engine.dispose()


async def _seed(maker, *, marked: int, unmarked: int, when: datetime,
                actor: str = "Азер", station: str = "rolls", org: str = "o1"):
    """Закрытый заказ с `marked` отмеченными и `unmarked` неотмеченными позициями."""
    async with maker() as s:
        order = Order(
            iiko_order_id=f"oo-{when.isoformat()}-{marked}-{unmarked}-{station}",
            org_id=org, number="1", status="Closed", created_at=when,
            delivered_at=when + timedelta(minutes=30),
        )
        order.items = [
            OrderItem(iiko_item_id=f"m{i}", product_id="p", name="Ролл",
                      station=station, station_ready_at=when)
            for i in range(marked)
        ] + [
            OrderItem(iiko_item_id=f"u{i}", product_id="p", name="Ролл",
                      station=station)
            for i in range(unmarked)
        ]
        s.add(order)
        await s.commit()

        for item in order.items[:marked]:
            s.add(OrderEvent(order_id=order.id, type="item_ready", org_id=org,
                             actor_name=actor, item_id=item.id, at=when))
        await s.commit()


async def test_coverage_counts_only_marked_items(client):
    """3 отмечено из 5 -> покрытие 60%."""
    await _seed(client.maker, marked=3, unmarked=2,
                when=datetime(2026, 8, 3, 12, 0))

    resp = await client.get(
        "/api/analytics/adoption?org_id=o1&from=2026-08-01&to=2026-08-31")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_items"] == 5
    assert data["marked_items"] == 3
    assert data["coverage_pct"] == 60.0


async def test_open_orders_are_not_counted(client):
    """Незакрытый заказ ещё готовится — в знаменатель не идёт."""
    async with client.maker() as s:
        order = Order(iiko_order_id="open-1", org_id="o1", number="9",
                      status="OnCooking", created_at=datetime(2026, 8, 3, 12, 0))
        order.items = [OrderItem(iiko_item_id="x", product_id="p",
                                 name="Ролл", station="rolls")]
        s.add(order)
        await s.commit()

    resp = await client.get(
        "/api/analytics/adoption?org_id=o1&from=2026-08-01&to=2026-08-31")
    assert resp.json()["total_items"] == 0


async def test_breakdown_by_person_and_station(client):
    """Разбивка по людям и станциям."""
    await _seed(client.maker, marked=2, unmarked=0,
                when=datetime(2026, 8, 3, 12, 0), actor="Азер", station="rolls")
    await _seed(client.maker, marked=1, unmarked=3,
                when=datetime(2026, 8, 3, 13, 0), actor="Мурад", station="pizza")

    data = (await client.get(
        "/api/analytics/adoption?org_id=o1&from=2026-08-01&to=2026-08-31")).json()

    by_person = {r["actor_name"]: r["marked"] for r in data["by_person"]}
    assert by_person == {"Азер": 2, "Мурад": 1}

    by_station = {r["station"]: r for r in data["by_station"]}
    assert by_station["rolls"]["total"] == 2
    assert by_station["rolls"]["marked"] == 2
    assert by_station["pizza"]["total"] == 4
    assert by_station["pizza"]["marked"] == 1
    assert by_station["pizza"]["coverage_pct"] == 25.0


async def test_breakdown_by_day(client):
    """По дням — в локальном времени филиала."""
    await _seed(client.maker, marked=1, unmarked=1,
                when=datetime(2026, 8, 2, 10, 0))
    await _seed(client.maker, marked=2, unmarked=0,
                when=datetime(2026, 8, 3, 10, 0))

    data = (await client.get(
        "/api/analytics/adoption?org_id=o1&from=2026-08-01&to=2026-08-31")).json()

    by_day = {r["date"]: r for r in data["by_day"]}
    assert by_day["2026-08-02"]["coverage_pct"] == 50.0
    assert by_day["2026-08-03"]["coverage_pct"] == 100.0


async def test_other_org_is_invisible(client):
    """Чужой филиал не попадает в цифры."""
    await _seed(client.maker, marked=1, unmarked=0,
                when=datetime(2026, 8, 3, 12, 0), org="o2")

    data = (await client.get(
        "/api/analytics/adoption?org_id=o1&from=2026-08-01&to=2026-08-31")).json()
    assert data["total_items"] == 0
    assert data["coverage_pct"] == 0.0


async def test_empty_range_returns_zeroes_not_error(client):
    """Пустой период — нули, а не деление на ноль."""
    data = (await client.get(
        "/api/analytics/adoption?org_id=o1&from=2026-01-01&to=2026-01-02")).json()
    assert data["total_items"] == 0
    assert data["marked_items"] == 0
    assert data["coverage_pct"] == 0.0
```

- [ ] **Step 2: Запустить тест и убедиться, что он падает**

Run: `cd backend && python -m pytest tests/test_adoption.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.adoption'`.

- [ ] **Step 3: Реализовать модуль**

Создать `backend/app/adoption.py` со следующим содержимым:

```python
from __future__ import annotations
from collections import defaultdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import Order, OrderEvent

router = APIRouter(prefix="/api/analytics", tags=["adoption"])

# Статусы, означающие, что заказ дошёл до конца и его точно готовили.
TERMINAL_STATUSES = {"Closed"}


def _parse_day(value: str, field: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=422,
                            detail=f"{field}: ожидается YYYY-MM-DD")


def _local_date(dt: datetime) -> date:
    """Дата в локальном времени филиала: в БД всё лежит в UTC."""
    return (dt + timedelta(hours=settings.iiko_tz_offset_hours)).date()


def _pct(marked: int, total: int) -> float:
    """Доля в процентах до десятой. Пустой период -> 0.0, а не деление на ноль."""
    if total <= 0:
        return 0.0
    return round(marked * 100.0 / total, 1)


@router.get("/adoption")
async def adoption(
    org_id: str,
    from_: str = Query(..., alias="from"),
    to: str = Query(...),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Покрытие KDS: какая доля приготовленных позиций получила отметку на экране.

    Знаменатель — позиции завершённых заказов (их точно готовили). Числитель —
    те, по которым живой человек нажал «Готово» (есть OrderEvent item_ready с
    актором). Разница и есть еда, сделанная мимо системы."""
    day_from = _parse_day(from_, "from")
    day_to = _parse_day(to, "to")

    # Границы с запасом в сутки по краям: отбор по локальной дате делаем в Python,
    # чтобы не зависеть от умения диалекта СУБД сдвигать таймзону.
    lo = datetime.combine(day_from, datetime.min.time()) - timedelta(days=1)
    hi = datetime.combine(day_to, datetime.max.time()) + timedelta(days=1)

    orders = (await session.execute(
        select(Order).where(
            Order.org_id == org_id,
            Order.created_at >= lo,
            Order.created_at <= hi,
        )
    )).scalars().all()

    finished = [
        o for o in orders
        if (o.status in TERMINAL_STATUSES or o.delivered_at is not None)
        and day_from <= _local_date(o.created_at) <= day_to
    ]

    # Позиции, по которым кто-то живой нажал «Готово».
    events = (await session.execute(
        select(OrderEvent).where(
            OrderEvent.type == "item_ready",
            OrderEvent.item_id.is_not(None),
        )
    )).scalars().all()
    actor_by_item: dict[int, str] = {
        e.item_id: e.actor_name for e in events
        if e.actor_name and e.actor_name.strip()
    }

    total = marked = 0
    per_person: dict[str, int] = defaultdict(int)
    per_day_total: dict[date, int] = defaultdict(int)
    per_day_marked: dict[date, int] = defaultdict(int)
    per_station_total: dict[str, int] = defaultdict(int)
    per_station_marked: dict[str, int] = defaultdict(int)

    for order in finished:
        day = _local_date(order.created_at)
        for item in order.items:
            station = item.station or "other"
            total += 1
            per_day_total[day] += 1
            per_station_total[station] += 1

            actor = actor_by_item.get(item.id)
            if actor:
                marked += 1
                per_day_marked[day] += 1
                per_station_marked[station] += 1
                per_person[actor] += 1

    return {
        "org_id": org_id,
        "from": day_from.isoformat(),
        "to": day_to.isoformat(),
        "total_items": total,
        "marked_items": marked,
        "coverage_pct": _pct(marked, total),
        "by_person": [
            {"actor_name": name, "marked": count}
            for name, count in sorted(per_person.items(),
                                      key=lambda kv: -kv[1])
        ],
        "by_day": [
            {
                "date": day.isoformat(),
                "total": per_day_total[day],
                "marked": per_day_marked[day],
                "coverage_pct": _pct(per_day_marked[day], per_day_total[day]),
            }
            for day in sorted(per_day_total)
        ],
        "by_station": [
            {
                "station": station,
                "total": per_station_total[station],
                "marked": per_station_marked[station],
                "coverage_pct": _pct(per_station_marked[station],
                                     per_station_total[station]),
            }
            for station in sorted(per_station_total)
        ],
    }
```

- [ ] **Step 4: Подключить роутер**

В `backend/app/main.py` рядом с подключением `heartbeat_router` (Task 4) добавить:

```python
from app.adoption import router as adoption_router

fastapi_app.include_router(adoption_router)
```

- [ ] **Step 5: Запустить тесты и убедиться, что проходят**

Run: `cd backend && python -m pytest tests/test_adoption.py -v`
Expected: PASS, 6 тестов.

- [ ] **Step 6: Прогнать весь бэкенд**

Run: `cd backend && python -m pytest -q`
Expected: весь набор зелёный.

- [ ] **Step 7: Коммит**

```bash
git add backend/app/adoption.py backend/app/main.py backend/tests/test_adoption.py
git commit -m "feat(adoption): метрика покрытия KDS — сколько еды готовится мимо экрана"
```

---

### Task 7: Панель покрытия в аналитике

**Files:**
- Create: `frontend/src/components/AdoptionPanel.jsx`
- Modify: `frontend/src/lib/api.js` (добавить вызов)
- Modify: `frontend/src/screens/Analytics.jsx` (смонтировать панель)
- Modify: `frontend/src/index.css` (стили)
- Test: `frontend/src/components/AdoptionPanel.test.jsx`

**Interfaces:**
- Consumes: `GET /api/analytics/adoption` (Task 6), `GET /api/screens` (Task 4).
- Produces: React-компонент `<AdoptionPanel orgId={string} from={string} to={string} />`.

`Analytics.jsx` — 886 строк; панель кладём отдельным компонентом и монтируем одной строкой, чтобы файл не рос дальше.

- [ ] **Step 1: Написать падающий тест**

Создать `frontend/src/components/AdoptionPanel.test.jsx`:

```jsx
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import AdoptionPanel from "./AdoptionPanel.jsx";
import { api } from "../lib/api.js";

const SAMPLE = {
  org_id: "o1",
  from: "2026-08-01",
  to: "2026-08-04",
  total_items: 500,
  marked_items: 190,
  coverage_pct: 38.0,
  by_person: [{ actor_name: "Азер", marked: 120 }],
  by_day: [{ date: "2026-08-03", total: 100, marked: 20, coverage_pct: 20.0 }],
  by_station: [{ station: "rolls", total: 300, marked: 150, coverage_pct: 50.0 }],
};

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "screens").mockResolvedValue([]);
});

describe("AdoptionPanel", () => {
  it("показывает процент покрытия крупной цифрой", async () => {
    vi.spyOn(api, "adoption").mockResolvedValue(SAMPLE);
    render(<AdoptionPanel orgId="o1" from="2026-08-01" to="2026-08-04" />);
    await waitFor(() => expect(screen.getByText("38%")).toBeTruthy());
  });

  it("показывает, сколько позиций прошло мимо экрана", async () => {
    vi.spyOn(api, "adoption").mockResolvedValue(SAMPLE);
    render(<AdoptionPanel orgId="o1" from="2026-08-01" to="2026-08-04" />);
    // 500 - 190 = 310 позиций мимо KDS
    await waitFor(() => expect(screen.getByText(/310/)).toBeTruthy());
  });

  it("показывает разбивку по людям", async () => {
    vi.spyOn(api, "adoption").mockResolvedValue(SAMPLE);
    render(<AdoptionPanel orgId="o1" from="2026-08-01" to="2026-08-04" />);
    await waitFor(() => expect(screen.getByText("Азер")).toBeTruthy());
  });

  it("сообщает об ошибке, а не показывает пустоту", async () => {
    vi.spyOn(api, "adoption").mockRejectedValue(new Error("HTTP 500"));
    render(<AdoptionPanel orgId="o1" from="2026-08-01" to="2026-08-04" />);
    await waitFor(() => expect(screen.getByText(/HTTP 500/)).toBeTruthy());
  });
});
```

- [ ] **Step 2: Запустить тест и убедиться, что он падает**

Run: `cd frontend && npm test src/components/AdoptionPanel.test.jsx`
Expected: FAIL — `Failed to resolve import "./AdoptionPanel.jsx"`.

- [ ] **Step 3: Добавить вызов в api.js**

В `frontend/src/lib/api.js` в объект `api` рядом с остальной аналитикой добавить:

```js
  adoption: (orgId, from, to) =>
    get(`/api/analytics/adoption?org_id=${encodeURIComponent(orgId)}&from=${from}&to=${to}`),
```

- [ ] **Step 4: Создать компонент**

Создать `frontend/src/components/AdoptionPanel.jsx`:

```jsx
import { useEffect, useState } from "react";
import { api } from "../lib/api.js";

/**
 * Покрытие KDS — главная цифра Фазы 0.
 *
 * Отвечает на вопрос, который до сих пор задать было некому: какая доля еды
 * реально проходит через экран, а какая готовится мимо него. Рядом — состояние
 * планшетов, чтобы «не отметил» можно было отличить от «экран был выключен».
 */
export default function AdoptionPanel({ orgId, from, to }) {
  const [data, setData] = useState(null);
  const [screens, setScreens] = useState([]);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    Promise.all([api.adoption(orgId, from, to), api.screens(orgId)])
      .then(([adoption, screenRows]) => {
        if (!alive) return;
        setData(adoption);
        setScreens(Array.isArray(screenRows) ? screenRows : []);
        setError(null);
      })
      .catch((e) => {
        if (alive) setError(e.message || String(e));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [orgId, from, to]);

  if (loading) return <p className="muted">Считаем покрытие KDS…</p>;
  if (error) return <p className="err-banner">Ошибка: {error}</p>;
  if (!data) return null;

  const missed = data.total_items - data.marked_items;
  // Порог из спеки: жёсткую блокировку включаем только выше 80%.
  const tone =
    data.coverage_pct >= 80 ? "ok" : data.coverage_pct >= 50 ? "warn" : "bad";

  return (
    <section className="adoption">
      <h2 className="adoption__title">Покрытие KDS</h2>

      <div className="adoption__head">
        <div className={`adoption__big adoption__big--${tone}`}>
          {Math.round(data.coverage_pct)}%
        </div>
        <div className="adoption__facts">
          <div>
            Отмечено на экране: <b>{data.marked_items}</b> из{" "}
            <b>{data.total_items}</b> позиций
          </div>
          <div>
            Прошло мимо KDS: <b>{missed}</b>
          </div>
          <div className="muted">Цель — 80%, до неё блокировку не включаем.</div>
        </div>
      </div>

      <h3 className="adoption__sub">По людям</h3>
      <table className="admin-table">
        <thead>
          <tr>
            <th>Сотрудник</th>
            <th>Отметил позиций</th>
          </tr>
        </thead>
        <tbody>
          {data.by_person.length === 0 && (
            <tr>
              <td colSpan={2} className="muted">
                Ни одной отметки за период
              </td>
            </tr>
          )}
          {data.by_person.map((r) => (
            <tr key={r.actor_name}>
              <td>{r.actor_name}</td>
              <td>{r.marked}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3 className="adoption__sub">По станциям</h3>
      <table className="admin-table">
        <thead>
          <tr>
            <th>Станция</th>
            <th>Всего</th>
            <th>Отмечено</th>
            <th>Покрытие</th>
          </tr>
        </thead>
        <tbody>
          {data.by_station.map((r) => (
            <tr key={r.station}>
              <td>{r.station}</td>
              <td>{r.total}</td>
              <td>{r.marked}</td>
              <td>{r.coverage_pct}%</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3 className="adoption__sub">Экраны</h3>
      <table className="admin-table">
        <thead>
          <tr>
            <th>Станция</th>
            <th>Кто за экраном</th>
            <th>Связь</th>
            <th>Звук</th>
          </tr>
        </thead>
        <tbody>
          {screens.length === 0 && (
            <tr>
              <td colSpan={4} className="muted">
                Ни один экран не выходил на связь
              </td>
            </tr>
          )}
          {screens.map((s) => (
            <tr key={s.screen_id}>
              <td>{s.station || "—"}</td>
              <td>{s.operator_name || "—"}</td>
              <td>{s.online ? "🟢 онлайн" : "🔴 офлайн"}</td>
              <td>{s.audio_ok ? "🔊 есть" : "🔇 НЕТ"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
```

- [ ] **Step 5: Добавить стили**

В конец `frontend/src/index.css` добавить:

```css
/* Панель покрытия KDS в аналитике. */
.adoption { margin: 24px 0; }
.adoption__title { font-size: 20px; margin-bottom: 12px; }
.adoption__sub { font-size: 15px; margin: 18px 0 8px; }
.adoption__head { display: flex; align-items: center; gap: 24px; flex-wrap: wrap; }
.adoption__big { font-size: 64px; font-weight: 800; line-height: 1; }
.adoption__big--ok { color: #27ae60; }
.adoption__big--warn { color: #b8860b; }
.adoption__big--bad { color: #c0392b; }
.adoption__facts { display: flex; flex-direction: column; gap: 4px; }
```

- [ ] **Step 6: Смонтировать в аналитике**

В `frontend/src/screens/Analytics.jsx` добавить импорт рядом с остальными импортами компонентов:

```jsx
import AdoptionPanel from "../components/AdoptionPanel.jsx";
```

и вставить панель первым блоком после кнопок выбора периода (сразу после закрывающего тега блока с кнопками «Сегодня / 7 дней / 30 дней», около строки 563):

```jsx
        <AdoptionPanel orgId={orgId} from={range.from} to={range.to} />
```

Если в области видимости переменная филиала называется не `orgId`, использовать ту, что уже применяется в соседних вызовах `api.stageStats(...)` в этом же файле.

- [ ] **Step 7: Запустить тесты и сборку**

Run: `cd frontend && npm test && npm run build`
Expected: PASS, 18 тестов; сборка проходит.

- [ ] **Step 8: Коммит**

```bash
git add frontend/src/components/AdoptionPanel.jsx frontend/src/components/AdoptionPanel.test.jsx frontend/src/lib/api.js frontend/src/screens/Analytics.jsx frontend/src/index.css
git commit -m "feat(adoption): панель покрытия KDS и состояния экранов в аналитике"
```

---

### Task 8: Заглушённый экран виден управляющему

**Files:**
- Modify: `backend/app/models.py` (колонка `audio_bad_since` у `ScreenHeartbeat`)
- Modify: `backend/app/main.py:22-39` (`_ADDED_COLUMNS`), запуск и остановка фоновой задачи
- Modify: `backend/app/heartbeat.py` (проставлять `audio_bad_since`)
- Create: `backend/app/screen_alerts.py`
- Test: `backend/tests/test_screen_alerts.py`

**Interfaces:**
- Consumes: `app.models.ScreenHeartbeat` (Task 4), `app.models.TelegramLink`, `app.telegram.send_message`, `app.alerts.MANAGER_ROLES`.
- Produces:
  - Колонка `screen_heartbeats.audio_bad_since` (TIMESTAMP, nullable) — момент, когда звук на экране пропал; `NULL`, пока звук жив.
  - `check_once() -> None` — один проход проверки, шлёт управляющим сообщения о немых экранах.
  - `start_screen_alerts() -> asyncio.Task`, `stop_screen_alerts(task) -> None` — по образцу `app.alerts`.
  - Интервал из `SCREEN_ALERTS_INTERVAL_SECONDS`; `<=0` или отсутствует — проверка **выключена** (как у `app.alerts`).

Это пункт 5 Фазы 0 из спеки: заглушить звук физически всё ещё можно, но это перестаёт быть бесплатным.

- [ ] **Step 1: Написать падающий тест**

Создать `backend/tests/test_screen_alerts.py`:

```python
from datetime import datetime, timedelta

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

from app import screen_alerts, telegram
from app.config import settings
from app.db import Base
import app.models  # noqa: F401  (регистрирует таблицы)
from app.models import ScreenHeartbeat, TelegramLink


@pytest_asyncio.fixture
async def memory_maker(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(screen_alerts, "SessionLocal", maker)
    screen_alerts._alerted.clear()
    yield maker
    await engine.dispose()


@pytest_asyncio.fixture
async def sent(monkeypatch):
    """Перехватываем отправку в Telegram — в сеть не ходим."""
    settings.telegram_bot_token = "test-token"
    box: list[tuple[str, str]] = []

    async def fake_send(chat_id, text):
        box.append((chat_id, text))
        return True

    monkeypatch.setattr(telegram, "send_message", fake_send)
    return box


async def _add_manager(maker, org="o1", tg_id="555"):
    async with maker() as s:
        s.add(TelegramLink(telegram_id=tg_id, employee_code="E1", name="Mgr",
                           org_id=org, role="MN0"))
        await s.commit()


async def test_disabled_by_default(monkeypatch):
    """Без SCREEN_ALERTS_INTERVAL_SECONDS цикл сразу выходит."""
    monkeypatch.delenv("SCREEN_ALERTS_INTERVAL_SECONDS", raising=False)
    await screen_alerts.run_screen_alerts()

    monkeypatch.setenv("SCREEN_ALERTS_INTERVAL_SECONDS", "0")
    await screen_alerts.run_screen_alerts()


async def test_alerts_when_screen_mute_over_threshold(memory_maker, sent):
    """Экран без звука дольше 3 минут -> управляющему летит сообщение."""
    await _add_manager(memory_maker)
    async with memory_maker() as s:
        s.add(ScreenHeartbeat(
            screen_id="s-1", station="rolls", org_id="o1",
            operator_name="Азер", audio_ok=False, wake_lock_ok=True,
            last_seen=datetime.utcnow(),
            audio_bad_since=datetime.utcnow() - timedelta(minutes=12),
        ))
        await s.commit()

    await screen_alerts.check_once()

    assert len(sent) == 1
    chat_id, text = sent[0]
    assert chat_id == "555"
    assert "rolls" in text
    assert "12" in text  # сколько минут молчит


async def test_no_alert_below_threshold(memory_maker, sent):
    """Минуту без звука — ещё не повод дёргать управляющего."""
    await _add_manager(memory_maker)
    async with memory_maker() as s:
        s.add(ScreenHeartbeat(
            screen_id="s-1", station="rolls", org_id="o1",
            audio_ok=False, last_seen=datetime.utcnow(),
            audio_bad_since=datetime.utcnow() - timedelta(minutes=1),
        ))
        await s.commit()

    await screen_alerts.check_once()
    assert sent == []


async def test_no_alert_for_offline_screen(memory_maker, sent):
    """Выключенный экран — не «заглушённый». Про него отдельный разговор."""
    await _add_manager(memory_maker)
    async with memory_maker() as s:
        s.add(ScreenHeartbeat(
            screen_id="s-1", station="rolls", org_id="o1",
            audio_ok=False,
            last_seen=datetime.utcnow() - timedelta(minutes=30),
            audio_bad_since=datetime.utcnow() - timedelta(minutes=30),
        ))
        await s.commit()

    await screen_alerts.check_once()
    assert sent == []


async def test_dedups_repeated_checks(memory_maker, sent):
    """Второй проход подряд не шлёт то же самое повторно."""
    await _add_manager(memory_maker)
    async with memory_maker() as s:
        s.add(ScreenHeartbeat(
            screen_id="s-1", station="rolls", org_id="o1",
            audio_ok=False, last_seen=datetime.utcnow(),
            audio_bad_since=datetime.utcnow() - timedelta(minutes=12),
        ))
        await s.commit()

    await screen_alerts.check_once()
    await screen_alerts.check_once()
    assert len(sent) == 1
```

- [ ] **Step 2: Запустить тест и убедиться, что он падает**

Run: `cd backend && python -m pytest tests/test_screen_alerts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.screen_alerts'`.

- [ ] **Step 3: Добавить колонку в модель**

В `backend/app/models.py` в класс `ScreenHeartbeat` (добавлен в Task 4) дописать последним полем:

```python
    # Момент, когда звук пропал. NULL — звук жив. По нему считаем, сколько
    # экран уже стоит немым, и решаем, пора ли дёргать управляющего.
    audio_bad_since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```

- [ ] **Step 4: Прописать колонку в миграцию**

В `backend/app/main.py` в список `_ADDED_COLUMNS` добавить последней строкой:

```python
    ("screen_heartbeats", "audio_bad_since", "TIMESTAMP"),
```

- [ ] **Step 5: Проставлять момент пропажи звука в хартбите**

В `backend/app/heartbeat.py` в обработчике `heartbeat`, **перед** строкой `row.audio_ok = body.audio_ok`, вставить:

```python
    # Засекаем момент, когда звук пропал, и сбрасываем засечку, когда вернулся.
    if body.audio_ok:
        row.audio_bad_since = None
    elif row.audio_bad_since is None:
        row.audio_bad_since = datetime.utcnow()
```

- [ ] **Step 6: Создать модуль алертов**

Создать `backend/app/screen_alerts.py`:

```python
from __future__ import annotations
import asyncio
import logging
import os
from datetime import datetime, timedelta

from sqlalchemy import select

from app import telegram
from app.alerts import MANAGER_ROLES
from app.db import SessionLocal
from app.models import ScreenHeartbeat, TelegramLink

log = logging.getLogger("saya.screen_alerts")

# Сколько экран должен простоять немым, чтобы это стало проблемой управляющего.
MUTE_THRESHOLD_SECONDS = 3 * 60
# Экран, молчавший дольше этого, считается выключенным, а не заглушённым.
ONLINE_WINDOW_SECONDS = 90
# Повторное напоминание про тот же экран — не чаще раза в час.
RENOTIFY_SECONDS = 60 * 60

# screen_id -> когда в последний раз слали про него сообщение.
_alerted: dict[str, datetime] = {}


def _read_interval() -> int:
    """Интервал проверки в секундах. <=0 (или не число) => проверка ОТКЛЮЧЕНА."""
    try:
        return int(os.environ.get("SCREEN_ALERTS_INTERVAL_SECONDS", "0") or "0")
    except (TypeError, ValueError):
        return 0


async def _managers(session, org_id: str | None) -> list[TelegramLink]:
    """Управляющие филиала. Без org_id адресата нет — молчим."""
    if not org_id:
        return []
    rows = (await session.execute(
        select(TelegramLink).where(
            TelegramLink.org_id == org_id,
            TelegramLink.role.in_(MANAGER_ROLES),
        )
    )).scalars().all()
    return list(rows)


async def check_once() -> None:
    """Один проход: ищем живые, но немые экраны и сообщаем о них управляющим."""
    now = datetime.utcnow()
    online_cutoff = now - timedelta(seconds=ONLINE_WINDOW_SECONDS)

    async with SessionLocal() as session:
        screens = (await session.execute(
            select(ScreenHeartbeat).where(ScreenHeartbeat.audio_ok.is_(False))
        )).scalars().all()

        for screen in screens:
            try:
                # Выключенный экран — не заглушённый, это другая история.
                if not screen.last_seen or screen.last_seen < online_cutoff:
                    continue
                if screen.audio_bad_since is None:
                    continue

                mute_seconds = (now - screen.audio_bad_since).total_seconds()
                if mute_seconds < MUTE_THRESHOLD_SECONDS:
                    continue

                last = _alerted.get(screen.screen_id)
                if last and (now - last).total_seconds() < RENOTIFY_SECONDS:
                    continue

                who = screen.operator_name or "никого нет"
                text = (
                    f"🔇 Экран «{screen.station or screen.screen_id}» без звука "
                    f"{int(mute_seconds // 60)} мин. За экраном: {who}"
                )
                for link in await _managers(session, screen.org_id):
                    if link.telegram_id:
                        await telegram.send_message(link.telegram_id, text)
                _alerted[screen.screen_id] = now
            except Exception:  # noqa: BLE001 — один битый экран не валит цикл
                log.exception("screen_alerts: check failed for %s",
                              screen.screen_id)


async def run_screen_alerts() -> None:
    """Фоновый цикл. Отключён по умолчанию (interval<=0)."""
    interval = _read_interval()
    if interval <= 0:
        log.info("screen alerts disabled (interval=%s)", interval)
        return
    log.info("screen alerts started: interval=%ss", interval)
    while True:
        await check_once()
        await asyncio.sleep(interval)


def start_screen_alerts() -> asyncio.Task:
    """Запускает run_screen_alerts() фоновой задачей."""
    return asyncio.create_task(run_screen_alerts())


async def stop_screen_alerts(task: asyncio.Task | None) -> None:
    """Отменяет фоновую задачу и дожидается завершения."""
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
```

- [ ] **Step 7: Подключить задачу к жизненному циклу приложения**

В `backend/app/main.py` добавить импорт рядом с `from app.alerts import start_alerts, stop_alerts`:

```python
from app.screen_alerts import start_screen_alerts, stop_screen_alerts
```

В обработчике `_create_tables` (после строки со `start_alerts()`) добавить:

```python
    # Алерты о немых экранах. Отключены по умолчанию
    # (SCREEN_ALERTS_INTERVAL_SECONDS<=0).
    fastapi_app.state.screen_alerts_task = start_screen_alerts()
```

В обработчике `_stop_poller` добавить:

```python
    await stop_screen_alerts(getattr(fastapi_app.state, "screen_alerts_task", None))
```

- [ ] **Step 8: Запустить тесты и убедиться, что проходят**

Run: `cd backend && python -m pytest tests/test_screen_alerts.py -v`
Expected: PASS, 5 тестов.

- [ ] **Step 9: Прогнать весь бэкенд**

Run: `cd backend && python -m pytest -q`
Expected: весь набор зелёный.

- [ ] **Step 10: Коммит**

```bash
git add backend/app/screen_alerts.py backend/app/models.py backend/app/heartbeat.py backend/app/main.py backend/tests/test_screen_alerts.py
git commit -m "feat(alerts): немой экран виден управляющему в TG"
```

**Включение на проде:** задача выключена по умолчанию. Чтобы включить, выставить
переменную окружения `SCREEN_ALERTS_INTERVAL_SECONDS=60` в Render.

---

## Ручная проверка на планшете (после выката)

Выполняется живьём на планшете кухни, автотестами не покрывается:

- [ ] Перезагрузить вкладку KDS → появляется красная плашка «ЗВУК НЕ РАБОТАЕТ», после касания она исчезает и приходит звук нового заказа.
- [ ] Оставить экран на 10 минут без касаний → планшет не гаснет, таймеры на карточках продолжают идти.
- [ ] Открыть аналитику → в таблице «Экраны» станция показана как 🟢 онлайн и 🔊 есть.
- [ ] Выключить вайфай на планшете на 2 минуты → в аналитике экран становится 🔴 офлайн.
- [ ] Убедиться, что в шапке кухонного экрана нет никакой кнопки звука.
- [ ] Выставить `SCREEN_ALERTS_INTERVAL_SECONDS=60` в Render, убавить громкость планшета до нуля и подождать 4 минуты → управляющему приходит «🔇 Экран … без звука».
- [ ] Установить и настроить **Fully Kiosk Browser**: автостарт при включении, блокировка кнопок громкости, перезапуск при падении, экран всегда включён. Организационный шаг из спеки — из браузера системную громкость Android защитить нельзя.

## После выката

Фаза 0 работает **неделю в одиночку**, без Фаз 1 и 2. Задача недели — получить
базовую цифру покрытия KDS, с которой потом сравнивать эффект от последствий.
Планы Фаз 1 и 2 пишутся после того, как цифра появится.
