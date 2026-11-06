import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

export default defineConfig({
  root: resolve(import.meta.dirname),
  plugins: [react()],
  define: { "process.env.NODE_ENV": '"production"' },
  build: {
    outDir: resolve(import.meta.dirname, "../../static"),
    emptyOutDir: false,
    lib: { entry: resolve(import.meta.dirname, "main.jsx"), formats: ["iife"], name: "ConexaoDPChat", fileName: () => "chat-widget.js", cssFileName: "chat-widget" },
    rollupOptions: { output: { assetFileNames: "chat-widget.css" } },
  },
});
