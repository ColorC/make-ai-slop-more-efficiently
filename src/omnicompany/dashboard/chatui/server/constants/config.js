/**
 * Environment Flag: Is Platform
 * Indicates if the app is running in Platform mode (hosted) or OSS mode (self-hosted)
 */
// [OMNI] 登录已剥离: 永远单用户平台模式(auth 中间件走单用户 bypass, 见 middleware/auth.js)。
// 硬编码 true, 不再依赖 VITE_IS_PLATFORM。
export const IS_PLATFORM = true;