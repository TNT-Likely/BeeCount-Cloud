/// <reference types="vite/client" />

// 由 vite.config.ts 的 define 注入，编译时替换成 package.json 里的版本字面量。
declare const __APP_VERSION__: string
