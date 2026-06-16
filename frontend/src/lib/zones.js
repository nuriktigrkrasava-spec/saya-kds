// Zone color map — reused across all screens.
export const ZONE_COLORS = {
  rolls: "#16a34a", // зелёный
  pizza: "#ea580c", // красно-оранжевый
  hot: "#f59e0b", // янтарный / жёлтый
  other: "#64748b", // серый
};

export const ZONE_LABELS = {
  rolls: "Роллы",
  pizza: "Пицца",
  hot: "Горячее",
  other: "Прочее",
};

export function zoneColor(station) {
  return ZONE_COLORS[station] || ZONE_COLORS.other;
}

export function zoneLabel(station) {
  return ZONE_LABELS[station] || ZONE_LABELS.other;
}
