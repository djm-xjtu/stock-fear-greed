// 数据源配置
//
// 页面默认读取随站点一起发布的静态 JSON（./data/*.json），零后端依赖。
// 若你自建了公网可达的 FastAPI 后端，把 LIVE_API 填成它的地址即可：
// 前端会优先请求实时接口（2.5s 超时），失败自动回落到静态快照。
window.LIVE_API = "";
window.STATIC_BASE = "./data";
