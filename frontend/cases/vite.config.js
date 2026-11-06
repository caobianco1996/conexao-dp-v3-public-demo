import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'node:path';

export default defineConfig({
  root: resolve(import.meta.dirname),
  plugins: [react()],
  build: {
    outDir: resolve(import.meta.dirname, '../../static/react-cases'),
    emptyOutDir: true,
    lib: {
      entry: resolve(import.meta.dirname, 'main.jsx'),
      formats: ['iife'],
      name: 'ConexaoDPCases',
      fileName: () => 'cases.js',
      cssFileName: 'cases',
    },
    rollupOptions: { output: { assetFileNames: 'cases.css' } },
  },
});
