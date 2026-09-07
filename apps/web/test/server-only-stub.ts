/**
 * `server-only` 在测试环境下的替身。
 *
 * 真包在被非 server 环境导入时会主动 throw，用来在构建期拦住"服务端模块
 * 泄漏到客户端 bundle"。这个保护由 `next build` 负责（CI 里跑），
 * 单测里只需要它闭嘴。
 */

export {};
