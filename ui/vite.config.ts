// The dev server proxies /api to the inspector API, so the browser sees one
// origin and the API needs no CORS. Both bind to 127.0.0.1 only (D19).
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const inspectorApi = process.env.EKB_INSPECTOR_URL ?? "http://127.0.0.1:8090";

export default defineConfig({
    plugins: [react()],
    server: {
        host: "127.0.0.1",
        port: 5173,
        strictPort: true,
        proxy: { "/api": inspectorApi },
    },
});
