import { Routes, Route } from "react-router-dom";
import Home from "./screens/Home.jsx";
import KitchenRolls from "./screens/KitchenRolls.jsx";
import KitchenPizza from "./screens/KitchenPizza.jsx";
import Assembler from "./screens/Assembler.jsx";
import Courier from "./screens/Courier.jsx";
import Admin from "./screens/Admin.jsx";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/rolls" element={<KitchenRolls />} />
      <Route path="/pizza" element={<KitchenPizza />} />
      <Route path="/assembler" element={<Assembler />} />
      <Route path="/courier" element={<Courier />} />
      <Route path="/admin" element={<Admin />} />
      <Route path="*" element={<Home />} />
    </Routes>
  );
}
