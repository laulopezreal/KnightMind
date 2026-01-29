import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Use local backend for development with Stockfish
// Set VITE_USE_LOCAL_API=true to use localhost:8000
const useLocalApi = process.env.VITE_USE_LOCAL_API === 'true';

// https://vite.dev/config/
export default defineConfig({
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
})
