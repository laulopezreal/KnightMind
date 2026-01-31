import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Home from './pages/Home';
import Openings from './pages/Openings';
import Engine from './pages/Engine';
import Puzzles from './pages/Puzzles';
import Layout from './components/Layout';
import { ChessUsernameProvider } from './context/ChessUsernameContext';

function App() {
  return (
    <ChessUsernameProvider>
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
    </ChessUsernameProvider>
  );
}

export default App;
