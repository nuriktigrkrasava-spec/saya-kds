import { Routes, Route, Navigate } from "react-router-dom";
import Home from "./screens/Home.jsx";
import KitchenRolls from "./screens/KitchenRolls.jsx";
import KitchenPizza from "./screens/KitchenPizza.jsx";
import Assembler from "./screens/Assembler.jsx";
import TgCourier from "./screens/TgCourier.jsx";
import Branches from "./screens/Branches.jsx";
import StationMap from "./screens/StationMap.jsx";
import CourierMap from "./screens/CourierMap.jsx";
import DemandMap from "./screens/DemandMap.jsx";
import Login from "./screens/Login.jsx";
import Track from "./screens/Track.jsx";
import EmployeeHistory from "./screens/EmployeeHistory.jsx";
import Dashboard from "./screens/Dashboard.jsx";
import Analytics from "./screens/Analytics.jsx";
import DeliveryZones from "./screens/DeliveryZones.jsx";
import { isAuthed, isManager } from "./lib/auth.js";

function RequireAuth({ children }) {
  if (!isAuthed()) return <Navigate to="/login" replace />;
  return children;
}

// Доступ только управляющим (аналитика, сотрудники, разметка, карта).
function RequireManager({ children }) {
  if (!isAuthed()) return <Navigate to="/login" replace />;
  if (!isManager()) return <Navigate to="/" replace />;
  return children;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/tg" element={<TgCourier />} />
      <Route path="/track/:id" element={<Track />} />
      <Route path="/" element={<RequireAuth><Home /></RequireAuth>} />
      <Route path="/rolls" element={<RequireAuth><KitchenRolls /></RequireAuth>} />
      <Route path="/pizza" element={<RequireAuth><KitchenPizza /></RequireAuth>} />
      <Route path="/assembler" element={<RequireAuth><Assembler /></RequireAuth>} />
      <Route path="/dashboard" element={<RequireManager><Dashboard /></RequireManager>} />
      <Route path="/analytics" element={<RequireManager><Analytics /></RequireManager>} />
      <Route path="/delivery-zones" element={<RequireManager><DeliveryZones /></RequireManager>} />
      <Route path="/admin" element={<Navigate to="/analytics" replace />} />
      <Route path="/branches" element={<RequireManager><Branches /></RequireManager>} />
      <Route path="/stations" element={<RequireManager><StationMap /></RequireManager>} />
      <Route path="/history" element={<RequireManager><EmployeeHistory /></RequireManager>} />
      <Route path="/map" element={<RequireAuth><CourierMap /></RequireAuth>} />
      <Route path="/demand" element={<RequireManager><DemandMap /></RequireManager>} />
      <Route path="*" element={<RequireAuth><Home /></RequireAuth>} />
    </Routes>
  );
}
