import { defineConfig } from 'vite';
const proxy = { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true } };
export default defineConfig({ server: { port: 5173, proxy }, preview: { proxy } });
