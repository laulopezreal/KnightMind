import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Layout from './components/Layout';
import { DataStateLoading } from './components/DataState';
import { ChessUsernameProvider } from './context/ChessUsernameContext';
import { PuzzleModeProvider } from './context/PuzzleModeContext';
import { ThemeProvider } from './context/ThemeContext';
import { ErrorBoundary } from './components/ErrorBoundary';

// Route-based code splitting: each page is loaded on demand so heavy
// dependencies (d3, recharts, chess.js, react-chessboard) stay out of the
// initial bundle downloaded on first visit.
const Home = lazy(() => import('./pages/Home'));
const Dashboard = lazy(() => import('./pages/Dashboard'));
const Openings = lazy(() => import('./pages/Openings'));
const Engine = lazy(() => import('./pages/Engine'));
const Puzzles = lazy(() => import('./pages/Puzzles'));
const Library = lazy(() => import('./pages/Library'));
const LibraryPuzzle = lazy(() => import('./pages/LibraryPuzzle'));
const Insights = lazy(() => import('./pages/Insights'));
const RatingInsights = lazy(() => import('./pages/RatingInsights'));
const Ops = lazy(() => import('./pages/Ops'));
const HowItWorks = lazy(() => import('./pages/HowItWorks'));

function App() {
  return (
    <ErrorBoundary>
      <ThemeProvider>
        <ChessUsernameProvider>
          <PuzzleModeProvider>
            <BrowserRouter>
              <Layout>
                <Suspense fallback={<DataStateLoading label="Loading page…" />}>
                  <Routes>
                    <Route path="/" element={<Home />} />
                    <Route path="/dashboard" element={<Dashboard />} />
                    <Route path="/openings" element={<Openings />} />
                    <Route path="/engine" element={<Engine />} />
                    <Route path="/library" element={<Library />} />
                    <Route path="/library/:puzzleId" element={<LibraryPuzzle />} />
                    <Route path="/puzzles" element={<Puzzles />} />
                    <Route path="/insights" element={<Insights />} />
                    <Route path="/rating-insights" element={<RatingInsights />} />
                    <Route path="/ops" element={<Ops />} />
                    <Route path="/how-it-works" element={<HowItWorks />} />
                  </Routes>
                </Suspense>
              </Layout>
            </BrowserRouter>
          </PuzzleModeProvider>
        </ChessUsernameProvider>
      </ThemeProvider>
    </ErrorBoundary>
  );
}

export default App;
