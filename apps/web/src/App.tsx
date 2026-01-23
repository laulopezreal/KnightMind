import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Home from './pages/Home';
import Openings from './pages/Openings';

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/openings" element={<Openings />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
