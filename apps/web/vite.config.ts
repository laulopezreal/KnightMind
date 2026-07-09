import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // Load env file based on `mode` in the current working directory.
  // Set the third parameter to '' to load all env regardless of the `VITE_` prefix.
  const env = loadEnv(mode, process.cwd(), '')
  const useLocalApi = env.VITE_USE_LOCAL_API === 'true'
  const apiTarget = useLocalApi ? 'http://localhost:8000' : 'https://knightmind-api.onrender.com'

  console.log('Using API target:', apiTarget)

  return {
    define: {
      // Expose the proxy target so the frontend can display it in diagnostics.
      '__API_TARGET__': JSON.stringify(apiTarget),
    },
    plugins: [react(), tailwindcss()],
    // No manualChunks: Rollup's automatic splitting already produces truly
    // lazy shared vendor chunks for the lazy routes. Object-form manualChunks
    // pulls each package's whole dependency graph (including React itself via
    // react-chessboard) into an eagerly-preloaded chunk, inflating first load.
    server: {
      proxy: {
        '/api': useLocalApi
          ? {
            target: 'http://localhost:8000',
            changeOrigin: true,
            rewrite: (path) => path.replace(/^\/api/, ''),
          }
          : {
            target: 'https://knightmind-api.onrender.com',
            changeOrigin: true,
            rewrite: (path) => path.replace(/^\/api/, ''),
          },
      },
    },
  }
})
