import path from 'path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// API と規則データは FastAPI（server/app.py）が返す。
// 開発時は `uvicorn server.app:app --port 8080` を別に立てて、そこへ流す。
const API_TARGET = process.env.API_TARGET || 'http://127.0.0.1:8080';

export default defineConfig({
  server: {
    port: 3000,
    host: '0.0.0.0',
    proxy: {
      '/api': { target: API_TARGET, changeOrigin: true },
      '/content': { target: API_TARGET, changeOrigin: true },
      '/healthz': { target: API_TARGET, changeOrigin: true },
    },
  },
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, '.') },
  },
  build: {
    // 本文の描画は素の React で完結するので、チャンク分割は不要
    chunkSizeWarningLimit: 700,
  },
});
