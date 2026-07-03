import { Routes, Route } from "react-router-dom";
import SearchPage from "./pages/SearchPage";
import EnterprisePage from "./pages/EnterprisePage";

export default function App() {
  return (
    <div className="page">
      <Routes>
        <Route path="/" element={<SearchPage />} />
        <Route path="/enterprise/:num" element={<EnterprisePage />} />
      </Routes>
    </div>
  );
}
