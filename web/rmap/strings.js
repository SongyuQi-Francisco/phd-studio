// Every research map UI string, Chinese and English, in one place (moves to the i18n framework of
// issue #49 as one module file). In the Chinese UI technical terms stay English: Gate, Kill criteria,
// Claim Gate, baseline, ablation, metric names. Extensions add their own with rmap.strings({...}).
const S = {
  zh: {
    "rmap.title": "研究地图",
    "rmap.rail": "研究地图 · 课题的问题、路径与验证",
    "rmap.empty.title": "还没有研究地图",
    "rmap.empty.hint": "每个课题一张图：问题 → 路径/假设 → 子线 → 实验 → 结果，标明哪些已验证、哪些没打通。",
    "rmap.project.new": "新建课题",
    "rmap.project.title": "课题名",
    "rmap.project.line": "研究线（可选）",
    "rmap.project.create": "创建",
    "rmap.project.cancel": "取消",
    "rmap.loading": "正在读取地图…",
    "rmap.error": "读不到这张地图：{msg}",
    "rmap.nodes": "{n} 个节点",
    "rmap.select.hint": "点一个节点看详情",
    "rmap.layers": "图层",

    "rmap.type.question": "问题",
    "rmap.type.hypothesis": "路径/假设",
    "rmap.type.subline": "子线",
    "rmap.type.experiment": "实验",
    "rmap.type.result": "结果",
    "rmap.type.decision": "决策",
    "rmap.type.kill": "Kill criteria",
    "rmap.type.open": "开放问题",
    "rmap.col.question": "问题",
    "rmap.col.hypothesis": "路径",
    "rmap.col.subline": "子线",
    "rmap.col.experiment": "实验",
    "rmap.col.result": "结果",

    "rmap.status.todo": "未开始",
    "rmap.status.active": "进行中",
    "rmap.status.verified": "已验证",
    "rmap.status.blocked": "没打通",
    "rmap.status.refuted": "证伪",
    "rmap.status.dropped": "放弃",

    "rmap.edge.refines": "细分",
    "rmap.edge.depends": "依赖",
    "rmap.edge.alternative": "备选",
    "rmap.edge.conflicts": "冲突",

    "rmap.evidence": "证据",
    "rmap.evidence.paper": "论文笔记",
    "rmap.evidence.note": "vault 笔记",
    "rmap.evidence.file": "文件",
    "rmap.evidence.url": "链接",
    "rmap.stance.supports": "支持",
    "rmap.stance.contradicts": "反对",
    "rmap.stance.context": "背景",

    "rmap.who.user": "你",
    "rmap.who.assistant": "助手",
    "rmap.who.cli": "命令行",
    "rmap.who.import": "导入",
    "rmap.who.system": "工作台",
  },
  en: {
    "rmap.title": "Research map",
    "rmap.rail": "Research map · questions, paths and what is verified",
    "rmap.empty.title": "No research maps yet",
    "rmap.empty.hint": "One map per project: question → path / hypothesis → sub-line → experiment → result, marked verified or blocked.",
    "rmap.project.new": "New project",
    "rmap.project.title": "Project name",
    "rmap.project.line": "Research line (optional)",
    "rmap.project.create": "Create",
    "rmap.project.cancel": "Cancel",
    "rmap.loading": "Loading the map…",
    "rmap.error": "Could not read this map: {msg}",
    "rmap.nodes": "{n} nodes",
    "rmap.select.hint": "Click a node for details",
    "rmap.layers": "Layers",

    "rmap.type.question": "Question",
    "rmap.type.hypothesis": "Path / hypothesis",
    "rmap.type.subline": "Sub-line",
    "rmap.type.experiment": "Experiment",
    "rmap.type.result": "Result",
    "rmap.type.decision": "Decision",
    "rmap.type.kill": "Kill criteria",
    "rmap.type.open": "Open question",
    "rmap.col.question": "Question",
    "rmap.col.hypothesis": "Path",
    "rmap.col.subline": "Sub-line",
    "rmap.col.experiment": "Experiment",
    "rmap.col.result": "Result",

    "rmap.status.todo": "Not started",
    "rmap.status.active": "In progress",
    "rmap.status.verified": "Verified",
    "rmap.status.blocked": "Blocked",
    "rmap.status.refuted": "Refuted",
    "rmap.status.dropped": "Dropped",

    "rmap.edge.refines": "Refines",
    "rmap.edge.depends": "Depends on",
    "rmap.edge.alternative": "Alternative",
    "rmap.edge.conflicts": "Conflicts",

    "rmap.evidence": "Evidence",
    "rmap.evidence.paper": "Paper note",
    "rmap.evidence.note": "Vault note",
    "rmap.evidence.file": "File",
    "rmap.evidence.url": "Link",
    "rmap.stance.supports": "Supports",
    "rmap.stance.contradicts": "Contradicts",
    "rmap.stance.context": "Context",

    "rmap.who.user": "You",
    "rmap.who.assistant": "Assistant",
    "rmap.who.cli": "CLI",
    "rmap.who.import": "Import",
    "rmap.who.system": "Workbench",
  },
};

export function lang() {
  return (document.documentElement.lang || "zh").toLowerCase().startsWith("en") ? "en" : "zh";
}

const warned = new Set();
// t("rmap.nodes", { n: 3 }) -> "3 个节点"; a missing key falls back to Chinese, then to the key itself.
export function t(key, params) {
  const l = lang();
  let s = S[l][key] ?? S.zh[key];
  if (s === undefined) {
    if (!warned.has(key)) { warned.add(key); console.warn("rmap: missing string", key); }
    s = key;
  }
  return params ? s.replace(/\{(\w+)\}/g, (m, k) => (params[k] ?? m)) : s;
}

export function addStrings(dict) {
  for (const [l, entries] of Object.entries(dict || {})) {
    if (!S[l]) S[l] = {};
    Object.assign(S[l], entries);
  }
}

// For the key-consistency check of #49: both languages must have the same keys.
export const _strings = S;
