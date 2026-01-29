import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Home from './pages/Home';
import Openings from './pages/Openings';
import Engine from './pages/Engine';
import Puzzles from './pages/Puzzles';
import Layout from './components/Layout';

function App() {
  return (
    <BrowserRouter>
      <Layout>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/openings" element={<Openings />} />
          <Route path="/engine" element={<Engine />} />
          <Route path="/puzzles" element={<Puzzles />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  );
}

export default App;
