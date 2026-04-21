import path from 'node:path'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

// 版本号读取优先级:
//   1. `VITE_APP_VERSION` 环境变量(CI / Docker 构建时注入)
//   2. Fallback:package.json 里的 version 字段(本地 dev 显示)
// 这样发版 tag 0.2.0 时,Docker build-arg VERSION=0.2.0 → ENV VITE_APP_VERSION
// → vite define → 客户端 bundle 里 `__APP_VERSION__` 就是 "0.2.0"。
const pkg = JSON.parse(
  readFileSync(path.resolve(__dirname, 'package.json'), 'utf-8')
) as { version: string }
const appVersion = process.env.VITE_APP_VERSION || pkg.version

export default defineConfig({
  define: {
    __APP_VERSION__: JSON.stringify(appVersion),
    // 本地 dev 时 process.env.VITE_APP_VERSION 为空,Vite 自动注入也拿不到值。
    // 显式 define 一遍兜底,保证 import.meta.env.VITE_APP_VERSION 永远有值
    // (本地 dev = pkg.version,生产 = CI 注入的 tag 版本)
    'import.meta.env.VITE_APP_VERSION': JSON.stringify(appVersion)
  },
  resolve: {
    alias: {
      '@beecount/api-client': path.resolve(__dirname, '../../packages/api-client/src'),
      '@beecount/ui': path.resolve(__dirname, '../../packages/ui/src'),
      '@beecount/web-features': path.resolve(__dirname, '../../packages/web-features/src'),
      // 强制 react / react-dom 走 web app 自己的 node_modules,避免
      // react-router-dom (7.x) 经由 pnpm 链接到另一份 react 实例,
      // 触发 "Cannot read properties of null (reading 'useState')" /
      // "Invalid hook call" 错误。
      react: path.resolve(__dirname, 'node_modules/react'),
      'react-dom': path.resolve(__dirname, 'node_modules/react-dom')
    },
    dedupe: ['react', 'react-dom', 'react/jsx-runtime']
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
