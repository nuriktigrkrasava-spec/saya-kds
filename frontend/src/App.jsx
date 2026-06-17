import { Routes, Route, Navigate } from "react-router-dom";
import Home from "./screens/Home.jsx";
import KitchenRolls from "./screens/KitchenRolls.jsx";
import KitchenPizza from "./screens/KitchenPizza.jsx";
import Assembler from "./screens/Assembler.jsx";
import TgCourier from "./screens/TgCourier.jsx";
import Admin from "./screens/Admin.jsx";
import Branches from "./screens/Branches.jsx";
import StationMap from "./screens/StationMap.jsx";
import CourierMap from "./screens/CourierMap.jsx";
import Login from "./screens/Login.jsx";
import { isAuthed } from "./lib/auth.js";

function RequireAuth({ children }) {
  if (!isAuthed()) return <Navigate to="/login" replace />;
  return children;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/tg" element={<TgCourier />} />
      <Route path="/" element={<RequireAuth><Home /></RequireAuth>} />
      <Route path="/rolls" element={<RequireAuth><KitchenRolls /></RequireAuth>} />
      <Route path="/pizza" element={<RequireAuth><KitchenPizza /></RequireAuth>} />
      <Route path="/assembler" element={<RequireAuth><Assembler /></RequireAuth>} />
      <Route path="/admin" element={<RequireAuth><Admin /></RequireAuth>} />
      <Route path="/branches" element={<RequireAuth><Branches /></RequireAuth>} />
      <Route path="/stations" element={<RequireAuth><StationMap /></RequireAuth>} />
      <Route path="/map" element={<RequireAuth><CourierMap /></RequireAuth>} />
      <Route path="*" element={<RequireAuth><Home /></RequireAuth>} />
    </Routes>
  );
}
