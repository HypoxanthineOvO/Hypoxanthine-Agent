import { fileURLToPath, URL } from "node:url";

import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  build: {
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("node_modules/echarts")) {
            return "echarts";
          }
          if (id.includes("node_modules/zrender")) {
            return "zrender";
          }
          if (id.includes("node_modules/vue-echarts")) {
            return "vue-echarts";
          }
          if (id.includes("node_modules/naive-ui")) {
            return "naive-ui";
          }
          if (id.includes("node_modules/vueuc")) {
            return "vueuc";
          }
          if (id.includes("node_modules/vooks") || id.includes("node_modules/vdirs") || id.includes("node_modules/seemly") || id.includes("node_modules/css-render")) {
            return "naive-ui-deps";
          }
          if (id.includes("node_modules/@monaco-editor")) {
            return "monaco";
          }
          return undefined;
        },
      },
    },
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    allowedHosts: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8765",
        changeOrigin: true,
      },
      "/ws": {
        target: "http://127.0.0.1:8765",
        ws: true,
        changeOrigin: true,
      },
    },
  },
  plugins: [
    vue(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["pwa-192x192.png", "pwa-512x512.png"],
      workbox: {
        maximumFileSizeToCacheInBytes: 5 * 1024 * 1024,
      },
      manifest: {
        name: "Hypo-Agent",
        short_name: "Hypo-Agent",
        description: "Single-user personal AI assistant",
        start_url: "/",
        display: "standalone",
        background_color: "#070a14",
        theme_color: "#070a14",
        icons: [
          {
            src: "pwa-192x192.png",
            sizes: "192x192",
            type: "image/png",
          },
          {
            src: "pwa-512x512.png",
            sizes: "512x512",
            type: "image/png",
          },
        ],
      },
    }),
  ],
});
