// sound.js — synthesized audio alerts for the kitchen (Web Audio API, no asset files).
//
// Browsers block autoplay until the user interacts with the page, so an
// AudioContext must be created/resumed from inside a user gesture. That is what
// unlockAudio() is for — it is wired to the bell toggle in ScreenHeader.
//
// All playback is a no-op unless sound is ON *and* the context is unlocked.

const STORAGE_KEY = "saya_sound_on";

let ctx = null; // shared AudioContext (lazily created on first unlock)

// --- preference persistence ----------------------------------------------

export function isSoundOn() {
  try {
    return localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function setSoundOn(on) {
  try {
    localStorage.setItem(STORAGE_KEY, on ? "1" : "0");
  } catch {
    /* ignore storage failures (private mode, etc.) */
  }
}

// --- context lifecycle ----------------------------------------------------

function AC() {
  return window.AudioContext || window.webkitAudioContext;
}

/**
 * Create or resume the AudioContext. MUST be called from a user gesture
 * (click/tap) the first time, otherwise the browser keeps it suspended.
 * Returns true if a usable, running context is available.
 */
export function unlockAudio() {
  const Ctor = AC();
  if (!Ctor) return false;
  try {
    if (!ctx) ctx = new Ctor();
    if (ctx.state === "suspended") {
      // resume() returns a promise; we don't need to await it here.
      ctx.resume().catch(() => {});
    }
    return true;
  } catch {
    return false;
  }
}

// Is the context present and (about to be) running?
function ready() {
  return !!ctx && ctx.state !== "closed";
}

// --- tone synthesis -------------------------------------------------------

/**
 * Play a single tone.
 * @param {number} freq      frequency in Hz
 * @param {number} start     start offset (seconds from now)
 * @param {number} dur       duration in seconds
 * @param {number} [gain]    peak gain (0..1)
 * @param {OscillatorType} [type]
 */
function tone(freq, start, dur, gain = 0.18, type = "sine") {
  if (!ctx) return;
  const t0 = ctx.currentTime + start;
  const osc = ctx.createOscillator();
  const env = ctx.createGain();

  osc.type = type;
  osc.frequency.setValueAtTime(freq, t0);

  // Quick attack, smooth exponential release to avoid clicks.
  env.gain.setValueAtTime(0.0001, t0);
  env.gain.exponentialRampToValueAtTime(gain, t0 + 0.012);
  env.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);

  osc.connect(env).connect(ctx.destination);
  osc.start(t0);
  osc.stop(t0 + dur + 0.02);
}

// --- public alerts --------------------------------------------------------

/** Pleasant two-tone "ding-dong" for a freshly arrived order. */
export function playNewOrder() {
  if (!isSoundOn() || !ready()) return;
  // Make sure a context suspended by the OS gets resumed before scheduling.
  if (ctx.state === "suspended") ctx.resume().catch(() => {});
  tone(880, 0, 0.18, 0.2, "sine"); // ding
  tone(1174.66, 0.16, 0.28, 0.2, "sine"); // dong (D6)
}

/** Urgent three-beep alarm for an order/item that has gone overdue. */
export function playOverdue() {
  if (!isSoundOn() || !ready()) return;
  if (ctx.state === "suspended") ctx.resume().catch(() => {});
  // 3 fast, slightly louder beeps with a harder timbre.
  tone(988, 0.0, 0.12, 0.24, "square");
  tone(988, 0.18, 0.12, 0.24, "square");
  tone(988, 0.36, 0.14, 0.24, "square");
}
