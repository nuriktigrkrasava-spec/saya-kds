import KitchenScreen from "../components/KitchenScreen.jsx";

export default function KitchenRolls() {
  return (
    <KitchenScreen
      title="Кухня «Роллы / Горячее»"
      accent="#16a34a"
      zones={["rolls", "hot"]}
      stations={["rolls", "hot"]}
    />
  );
}
