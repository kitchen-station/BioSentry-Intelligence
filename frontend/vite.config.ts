import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vitest/config'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // The vendored UI kit's behaviours and graphs (plain ES modules; types in src/types/hw-ui.d.ts).
  resolve: { alias: { 'hw-ui': fileURLToPath(new URL('./vendor/hw-ui/src/js/index.js', import.meta.url)) } },
  test: {
    setupFiles: ['./test/setup.ts'],
    coverage: {
      provider: 'v8',
      include: ['src/**/*.{ts,tsx}'],
      // main.tsx only mounts <App /> into index.html; the type files hold no runtime code.
      exclude: ['src/**/*.test.{ts,tsx}', 'src/main.tsx', 'src/types/**', 'src/vite-env.d.ts'],
      thresholds: { lines: 100 },
      reporter: ['text'],
    },
  },
})
