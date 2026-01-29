import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Home from './pages/Home';
import Openings from './pages/Openings';
import Engine from './pages/Engine';
import Puzzles from './pages/Puzzles';

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/openings" element={<Openings />} />
        <Route path="/engine" element={<Engine />} />
        <Route path="/puzzles" element={<Puzzles />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
