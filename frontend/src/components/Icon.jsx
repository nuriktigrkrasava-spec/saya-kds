// Единственный источник иконок КДС. Эмодзи на кухонных планшетах не годятся:
// они рисуются шрифтом системы (на Android-планшетах филиала часть просто
// квадратики), не наследуют цвет статуса и не масштабируются под размер строки.
// Здесь — штриховые SVG одного семейства: stroke 1.75, currentColor, 24×24.

const PATHS = {
  // Тип заказа. Глифы намеренно простые: на карточке они живут в 16px, где
  // «честный» мопед или тарелка со столовыми приборами превращаются в кашу.
  delivery: (
    <>
      <path d="M12 21s6.5-6.1 6.5-10.5a6.5 6.5 0 0 0-13 0C5.5 14.9 12 21 12 21Z" />
      <circle cx="12" cy="10.5" r="2.4" />
    </>
  ),
  dinein: (
    <>
      <path d="M7 3v7M10.5 3v7M8.75 10v11" />
      <path d="M17 3c-1.6 1.4-2.2 3.4-2.2 5.6 0 1.7.8 2.6 2.2 2.6V21" />
    </>
  ),
  pickup: (
    <>
      <path d="M5.5 8h13l-1 12h-11l-1-12Z" />
      <path d="M9 8V6.2a3 3 0 0 1 6 0V8" />
    </>
  ),
  aggregator: (
    <>
      <path d="M4 8.2 12 4.5l8 3.7v7.6L12 19.5l-8-3.7V8.2Z" />
      <path d="M4 8.2 12 12l8-3.8M12 12v7.5" />
    </>
  ),
  // Состояния
  clock: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </>
  ),
  preorder: (
    <>
      <path d="M4 7a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v4" />
      <path d="M4 7v11a2 2 0 0 0 2 2h6M8 3v4M16 3v4M4 11h16" />
      <circle cx="17" cy="17" r="4" />
      <path d="M17 15.3V17l1.2.9" />
    </>
  ),
  check: <path d="m5 12.5 4.5 4.5L19 7.5" />,
  alert: (
    <>
      <path d="M12 4.5 2.8 20h18.4L12 4.5Z" />
      <path d="M12 10v4.5M12 17.4v.1" />
    </>
  ),
  fire: (
    <>
      <path d="M12 3s5 4.2 5 9a5 5 0 0 1-10 0c0-1.6.6-3 1.4-4.1.4 1.2 1.2 2 2.1 2.2C10 8.3 12 6 12 3Z" />
    </>
  ),
  note: (
    <>
      <path d="M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5V15a1.5 1.5 0 0 1-1.5 1.5H9L4.5 20V5.5Z" />
      <path d="M8 9h8M8 12.5h5" />
    </>
  ),
  ban: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="m6.2 6.2 11.6 11.6" />
    </>
  ),
  card: (
    <>
      <rect x="3" y="6" width="18" height="12" rx="2" />
      <path d="M3 10h18M6.5 14.5h3" />
    </>
  ),
  courier: (
    <>
      <circle cx="12" cy="8" r="3.5" />
      <path d="M5 20c0-3.3 3.1-5.5 7-5.5s7 2.2 7 5.5" />
    </>
  ),
  // Навигация и служебное
  arrowLeft: <path d="M19 12H5m0 0 6-6m-6 6 6 6" />,
  arrowRight: <path d="M5 12h14m0 0-6-6m6 6-6 6" />,
  station: (
    <>
      <path d="M4 10h16M6 10V7.5a6 6 0 0 1 12 0V10" />
      <path d="M5 10v7a3 3 0 0 0 3 3h8a3 3 0 0 0 3-3v-7" />
    </>
  ),
  assembly: (
    <>
      <path d="M4 8.5 12 4l8 4.5v7L12 20l-8-4.5v-7Z" />
      <path d="M8 6.2 16 11v8" />
    </>
  ),
  mute: (
    <>
      <path d="M4 9.5h3.5L12 5.5v13L7.5 14.5H4v-5Z" />
      <path d="m16 9.5 5 5m0-5-5 5" />
    </>
  ),
  map: (
    <>
      <path d="M9 4 3.5 6v14L9 18l6 2 5.5-2V4L15 6 9 4Z" />
      <path d="M9 4v14M15 6v14" />
    </>
  ),
};

/**
 * @param {string} name  ключ из PATHS
 * @param {number} size  размер в px (иконка квадратная)
 */
export default function Icon({ name, size = 20, className = "", ...rest }) {
  const path = PATHS[name];
  if (!path) return null;
  return (
    <svg
      className={`icon ${className}`.trim()}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={size <= 18 ? 2 : 1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {path}
    </svg>
  );
}
