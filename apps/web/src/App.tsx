import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Home from './pages/Home';
import Dashboard from './pages/Dashboard';
import Openings from './pages/Openings';
import Engine from './pages/Engine';
import Puzzles from './pages/Puzzles';
import Insights from './pages/Insights';
import RatingInsights from './pages/RatingInsights';
import Ops from './pages/Ops';
import Layout from './components/Layout';
import { ChessUsernameProvider } from './context/ChessUsernameContext';
import { PuzzleModeProvider } from './context/PuzzleModeContext';
import { ThemeProvider } from './context/ThemeContext';
import { ErrorBoundary } from './components/ErrorBoundary';

function App() {
  return (
    <ErrorBoundary>
      <ThemeProvider>
        <ChessUsernameProvider>
          <PuzzleModeProvider>
            <BrowserRouter>
              <Layout>
                <Routes>
                  <Route path="/" element={<Home />} />
                  <Route path="/dashboard" element={<Dashboard />} />
                  <Route path="/openings" element={<Openings />} />
                  <Route path="/engine" element={<Engine />} />
                  <Route path="/puzzles" element={<Puzzles />} />
                  <Route path="/insights" element={<Insights />} />
                  <Route path="/rating-insights" element={<RatingInsights />} />
                  <Route path="/ops" element={<Ops />} />
                </Routes>
              </Layout>
            </BrowserRouter>
          </PuzzleModeProvider>
        </ChessUsernameProvider>
      </ThemeProvider>
    </ErrorBoundary>
  );
}

export default App;
