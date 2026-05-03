/**
 * 最小 qrcode 类型声明 — 只覆盖我们使用的方法。
 * 避免依赖 @types/qrcode(如果未来想要全量类型,装它即可)。
 */
declare module 'qrcode' {
  export type QRCodeErrorCorrectionLevel = 'L' | 'M' | 'Q' | 'H'
  export type QRCodeRenderersOptions = {
    width?: number
    margin?: number
    errorCorrectionLevel?: QRCodeErrorCorrectionLevel
    color?: { dark?: string; light?: string }
  }
  export function toDataURL(
    text: string,
    options?: QRCodeRenderersOptions,
  ): Promise<string>
  export function toCanvas(
    canvas: HTMLCanvasElement,
    text: string,
    options?: QRCodeRenderersOptions,
  ): Promise<void>
  const _default: {
    toDataURL: typeof toDataURL
    toCanvas: typeof toCanvas
  }
  export default _default
}
