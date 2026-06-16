import KitchenScreen from "../components/KitchenScreen.jsx";

export default function KitchenPizza() {
  return (
    <KitchenScreen
      title="Кухня «Пицца»"
      accent="#ea580c"
      zones={["pizza"]}
      stations={["pizza"]}
    />
  );
}
