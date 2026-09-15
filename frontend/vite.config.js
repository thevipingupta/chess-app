import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Local dev: proxy API calls to the FastAPI backend on port 8000
    // On Railway: frontend is served by FastAPI itself, so relative URLs work natively
    proxy: {
      '/auth':     'http://localhost:8000',
      '/game':     'http://localhost:8000',
      '/puzzles':  'http://localhost:8000',
      '/analysis': 'http://localhost:8000',
      '/health':   'http://localhost:8000',
    },
  },
})
