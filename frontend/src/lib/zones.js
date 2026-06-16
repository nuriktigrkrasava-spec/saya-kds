// Zone color map — reused across all screens (dots, accents, badges).
export const ZONE_COLORS = {
  rolls: "#16a34a", // зелёный
  pizza: "#e8552d", // красно-оранжевый (ember)
  hot: "#f59e0b", // янтарный / жёлтый
  other: "#64748b", // сланцево-серый
};

export const ZONE_LABELS = {
  rolls: "Роллы",
  pizza: "Пицца",
  hot: "Горячее",
  other: "Прочее",
};

export const ZONE_ICONS = {
  rolls: "🍣",
  pizza: "🍕",
  hot: "🍜",
  other: "🍽️",
};

export function zoneColor(station) {
  return ZONE_COLORS[station] || ZONE_COLORS.other;
}

export function zoneLabel(station) {
  return ZONE_LABELS[station] || ZONE_LABELS.other;
}

export function zoneIcon(station) {
  return ZONE_ICONS[station] || ZONE_ICONS.other;
}
