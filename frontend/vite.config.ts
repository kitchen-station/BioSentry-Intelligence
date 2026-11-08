import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  test: {
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
