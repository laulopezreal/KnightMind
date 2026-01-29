import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // Load env file based on `mode` in the current working directory.
  // Set the third parameter to '' to load all env regardless of the `VITE_` prefix.
  const env = loadEnv(mode, process.cwd(), '')
  const useLocalApi = env.VITE_USE_LOCAL_API === 'true'

  console.log('Using API target:', useLocalApi ? 'http://localhost:8000' : 'https://knightmind-api.onrender.com')

  return {
    plugins: [react(), tailwindcss()],
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
