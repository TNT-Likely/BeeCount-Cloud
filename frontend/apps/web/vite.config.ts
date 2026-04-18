import path from 'node:path'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

// 从 package.json 读版本号注入到客户端。build / dev 都会拿到。
const pkg = JSON.parse(
  readFileSync(path.resolve(__dirname, 'package.json'), 'utf-8')
) as { version: string }

export default defineConfig({
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version)
  },
  resolve: {
    alias: {
      '@beecount/api-client': path.resolve(__dirname, '../../packages/api-client/src'),
      '@beecount/ui': path.resolve(__dirname, '../../packages/ui/src'),
      '@beecount/web-features': path.resolve(__dirname, '../../packages/web-features/src')
    }
  },
  server: {
    port: 5173,
    host: true,
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true
      }
    }
  }
})
