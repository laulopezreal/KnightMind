import { lazy, Suspense, type ComponentType } from 'react';
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
//
// After a redeploy, a tab holding the previous index.html can 404 on old
// hashed chunk URLs when it navigates to a not-yet-visited route. React.lazy
// caches the rejection, so the ErrorBoundary's retry can never succeed —
// instead, reload once to pick up the fresh index.html. The sessionStorage
// guard prevents a reload loop if the import failure has another cause.
const RELOAD_GUARD = 'chunk-reload';
function lazyPage(load: () => Promise<{ default: ComponentType }>) {
  return lazy(() =>
    load().then((module) => {
      sessionStorage.removeItem(RELOAD_GUARD);
      return module;
    }).catch((error) => {
      if (sessionStorage.getItem(RELOAD_GUARD)) {
        throw error;
      }
      sessionStorage.setItem(RELOAD_GUARD, '1');
      window.location.reload();
      // Keep Suspense showing the loading state until the reload lands.
      return new Promise<{ default: ComponentType }>(() => {});
    })
  );
}

const Home = lazyPage(() => import('./pages/Home'));
const Dashboard = lazyPage(() => import('./pages/Dashboard'));
const Openings = lazyPage(() => import('./pages/Openings'));
const Engine = lazyPage(() => import('./pages/Engine'));
const Puzzles = lazyPage(() => import('./pages/Puzzles'));
const Library = lazyPage(() => import('./pages/Library'));
const LibraryPuzzle = lazyPage(() => import('./pages/LibraryPuzzle'));
const Insights = lazyPage(() => import('./pages/Insights'));
const RatingInsights = lazyPage(() => import('./pages/RatingInsights'));
const Ops = lazyPage(() => import('./pages/Ops'));
const HowItWorks = lazyPage(() => import('./pages/HowItWorks'));

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
