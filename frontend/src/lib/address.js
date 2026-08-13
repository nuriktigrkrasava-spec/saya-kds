// Подъезд, этаж, квартиру и домофон iiko отдаёт по-разному: у заказов операторов
// это отдельные поля, у заказов с сайта и из приложения — свободный текст внутри
// одной строки («кв.906,эт.9», «4 подъзд, 108 квартира», «этаж-3»). Курьеру в
// обоих случаях приходит длинная строка, в которой на бегу это не выцепить.
// Здесь достаём детали, чтобы показать их отдельно и крупно.

// Число ПОСЛЕ слова: «кв. 110», «кв.906», «этаж-3», «под 2».
const after = (words) =>
  new RegExp(`(?:${words})\\s*\\.?\\s*(?:/\\s*оф(?:ис)?\\.?)?\\s*[-–—№]?\\s*(\\d+[а-я]?)`, "i");

// Число ПЕРЕД словом: «5 подъезд», «108 квартира», «4 этаж».
const before = (words) => new RegExp(`(\\d+[а-я]?)\\s*[-–—]?\\s*(?:${words})`, "i");

// «подъзд» — не опечатка здесь, а как это приходит с сайта.
const PATTERNS = {
  flat: [after("квартира|кв"), before("квартира")],
  entrance: [after("подъезд|подъзд|подъед|под"), before("подъезд|подъзд|подъед")],
  floor: [after("этаж|эт"), before("этаж")],
};

const DOORPHONE = /(?:домофон|дфн)\s*\.?\s*[-–—]?\s*([#\wа-я]+)/i;

/**
 * Детали адреса из строки: {flat, entrance, floor, doorphone}. Чего нет — null.
 */
export function addressParts(address) {
  const s = (address || "").trim();
  const out = { flat: null, entrance: null, floor: null, doorphone: null };
  if (!s) return out;

  for (const [key, regexps] of Object.entries(PATTERNS)) {
    for (const re of regexps) {
      const m = re.exec(s);
      if (m) {
        out[key] = m[1];
        break;
      }
    }
  }
  const d = DOORPHONE.exec(s);
  if (d) out.doorphone = d[1];
  return out;
}

/** Есть ли что показывать плашками. */
export function hasAddressParts(parts) {
  return Boolean(parts.flat || parts.entrance || parts.floor || parts.doorphone);
}
