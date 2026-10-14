import { lazy, Suspense } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
const Home = lazy(() => import("./screens/Home.jsx"));
const KitchenRolls = lazy(() => import("./screens/KitchenRolls.jsx"));
const KitchenPizza = lazy(() => import("./screens/KitchenPizza.jsx"));
const Assembler = lazy(() => import("./screens/Assembler.jsx"));
const TgCourier = lazy(() => import("./screens/TgCourier.jsx"));
const Branches = lazy(() => import("./screens/Branches.jsx"));
const StationMap = lazy(() => import("./screens/StationMap.jsx"));
const CourierMap = lazy(() => import("./screens/CourierMap.jsx"));
const DemandMap = lazy(() => import("./screens/DemandMap.jsx"));
const Login = lazy(() => import("./screens/Login.jsx"));
const Track = lazy(() => import("./screens/Track.jsx"));
const EmployeeHistory = lazy(() => import("./screens/EmployeeHistory.jsx"));
const Dashboard = lazy(() => import("./screens/Dashboard.jsx"));
const Analytics = lazy(() => import("./screens/Analytics.jsx"));
const DeliveryZones = lazy(() => import("./screens/DeliveryZones.jsx"));
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
    <Suspense fallback={<p role="status">Загрузка…</p>}>
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
      <Route path="/map" element={<RequireManager><CourierMap /></RequireManager>} />
      <Route path="/demand" element={<RequireManager><DemandMap /></RequireManager>} />
      <Route path="*" element={<RequireAuth><Home /></RequireAuth>} />
    </Routes>
    </Suspense>
  );
}
