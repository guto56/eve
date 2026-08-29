import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// O build vai direto para dentro do pacote Python: o daemon serve a interface
// como arquivo estático, sem servidor separado em produção.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "../src/eve/web/static",
    emptyOutDir: true,
  },
  server: {
    port: 5273,
    proxy: {
      "/api": "http://127.0.0.1:4242",
      "/ws": { target: "ws://127.0.0.1:4242", ws: true },
    },
  },
});
