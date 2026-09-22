import { defineConfig } from "vite";
export default defineConfig({
  base: "./",
  build: {
    // Stable neutral names also comply with the App's private-path exclusion gate.
    rollupOptions: { output: { entryFileNames: "assets/editor.js", chunkFileNames: "assets/chunk-[hash].js", assetFileNames: "assets/editor.[ext]" } },
  },
});
