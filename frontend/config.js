// 数据源配置
//
// 页面默认读取随站点一起发布的静态 JSON（./data/*.json），零后端依赖 —— 因为 FaaS
// 后端域名（*.cn-east-fn.bytedance.net）是内网域名，公网浏览器无法直连，
// 直接请求会挂起并报 Failed to fetch。
//
// 如需接实时后端：把 LIVE_API 设为后端地址，页面会先尝试实时接口（2.5s 超时），
// 失败则自动回落到静态快照，不会白屏。
window.LIVE_API = "";
window.STATIC_BASE = "./data";
