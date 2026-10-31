import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite' // 1. Import Tailwind Vite Plugin

// https://vite.dev/config/
// File config nay chay o Node, nhung du an khong cai `@types/node` - khai bao
// tai cho con re hon la keo ca goi type ve chi de doc mot bien.
declare const process: { env: Record<string, string | undefined> }

// ⛔ SO PHIEN BAN DEN TU BUOC BUILD, khong phai tu package.json.
// `frontend/package.json` van la "0.0.0" va khong ai cap nhat no; so that nam
// o `TKAUTO_APP_VERSION`, bien ma script build dat truoc khi goi Tauri (xem
// scripts/build_licensed_unsigned_release.ps1). Chay dev thi khong co bien do,
// nen hien "dev" - noi that hon la bia mot so.
const APP_VERSION = process.env.TKAUTO_APP_VERSION || 'dev'

export default defineConfig({
  define: {
    __APP_VERSION__: JSON.stringify(APP_VERSION),
  },
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
  },
  plugins: [
    react(),
    tailwindcss(), // 2. Đăng ký plugin vào hệ thống compiler của Vite
  ],
})
