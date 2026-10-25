# 术语表 · Glossary

> 状态：**草案，待确认**（#49）。确认后 #50–#52 按这里逐页抽取和翻译；之后新增文案也按这里写。
> 写法和检查方法见 [docs/i18n.md](i18n.md)。标 ⚖️ 的行写的是建议，需要你在「待你决定」里定。
> 几个页面共用的术语已放进 `web/locales/*/common.json`（`common.term.*`、`common.status.*` 等），按这里的结论改。

## 规则

1. **中文界面：研究和技术术语保留英文，连接和说明性文字用中文。** 例：「Claim Gate 审计」「Kill criteria 体检」「CFP」「camera-ready」「T1 · 本周」「h-index」。
2. **工作台自己的页面、按钮和状态用中文**，它们是产品词，不是学术术语。例：阅读清单、收件箱、在读、读完、想法、设置。
3. **英文界面全部用英文**，用论文和会议网站上的惯用写法，句子短、用主动语态，按钮用动词开头，标签用 Sentence case（只大写首字母和专有名词）。
4. **专有名词两种界面都不变**：PhD Studio、Claude、Codex、Gemini、DeepSeek、GLM、Zotero、Obsidian、vault、arXiv、OpenReview、ACL Anthology、OpenAlex、Google Scholar、CORE、CCF、ccfddl、DBLP、Idea Radar、Lab Tracker。
5. **机器值不翻译**：YAML 字段和取值（`queued`、`T1`、`kill`）、API 值、文件名（`Reading List.md`、`plan.yaml`）、front matter、vault 文件夹名（Review Packets、Evidence）。
6. **中文里夹英文时**，英文两侧加空格（「定 Tier」「Gate 完成 2/3」），中文标点照常用全角。
7. **同一个概念只用一种写法**（见文末「要统一的写法」）；同一种写法也不要表示两个概念。

## 待你决定

每条给出建议（✅）和备选。定了之后我把结论写回表格、删掉 ⚖️。

| # | 问题 | 建议 | 备选 |
|---|---|---|---|
| D1 | 「Tier」这个词本身（不是 T1/T2/T3） | ✅ 中文界面写 Tier：「定 Tier」「按 Tier 排」 | 保留「优先级」：「定优先级」 |
| D2 | 阅读状态：收件箱 / 待读 / 在读 / 读完 / 稍后 / 不读 | ✅ 保留中文（产品状态，不是术语）；「已读」统一成「读完」 | Inbox / Queued / Reading / Done / Snoozed / Dismissed |
| D3 | 一句话收获（读完时写的一句话） | ✅ Takeaway | 保留「一句话收获」 |
| D4 | 里程碑的四种类型：里程碑 / Gate / Kill 检查 / 截止 | ✅ 里程碑 / Gate / Kill 检查点 / 截止（统一成「Kill 检查点」） | 全英文：Milestone / Gate / Kill check / Deadline |
| D5 | 会议截稿 | ✅ 「截稿」不变；两轮截稿写「Abstract 截稿 / Full paper 截稿」（避免和论文的「摘要」「全文」撞） | 保留「摘要截止 / 全文截止」；或用「DDL」 |
| D6 | 投稿目标 | ✅ 保留「投稿目标」，「目标」「我的目标」都统一成它 | Target |
| D7 | 会议的赛道（main track、Findings、workshop） | ✅ Track：「Track 与投稿要求」「Main track」 | 保留「赛道」「主会」 |
| D8 | 路线图 | ✅ 统一成「路线图」，按钮也不再写 Roadmap | 统一成 Roadmap |
| D9 | 想法 / idea | ✅ 随手记的叫「想法」；整理成的笔记叫「Idea 笔记」 | 全部用 idea；或全部用想法 |
| D10 | 阅读器的第三个版本「中文」（只看译文） | ✅ 「译文」，英文界面 Translation（不写死语言） | 保留「中文」 |
| D11 | 追踪页的几个词：追踪 / 巡检 / 雷达 / 跟踪 | ✅ 页面叫「追踪」；每周跑一次叫「巡检」；巡检结果那一页叫「本周雷达」；「跟踪学者」统一成「追踪的学者」 | 全部换成英文 Tracker / run / Radar |
| D12 | 实验室 / lab | ✅ 统一成「实验室」，只有专名 Lab Tracker 不变 | 统一成 lab |
| D13 | 学术指标 | ✅ h-index、i10-index 用英文；被引、引用统一成「被引」 | 「h 指数」保留中文 |
| D14 | 研究地图（#45）的节点类型和状态 | ✅ 中文，Kill criteria 除外（见「研究地图」表） | 节点类型用英文：Question / Hypothesis / Branch / Experiment / Result / Decision / Open question |
| D15 | 工作台写进 vault 的文件（现在是 `Reading List.md`）用哪种语言 | ✅ `studio.toml` 的 `[profile] vault_language` 固定一种，默认中文，两台 Mac 写出来一样（已实现）。注意：这个文件原来的标题是英文（# Reading List、## Inbox），改成中文后，指向 `#Reading` 这类小节的链接要改一次 | 默认英文（和原来一样，链接不用改）；或 `ui`：跟随各自 Mac 的界面语言，两台设置不同时会来回改写 |
| D16 | 英文界面下，划词翻译和双语 PDF 还翻成中文吗 | ✅ 先不变（目标语言是中文）；以后需要时再加「翻译成」设置 | 跟随界面语言 |
| D17 | 研究线卡片上的「第一个实验」「证据」 | ✅ First Experiment、Evidence，和笔记小节、vault 文件夹同名 | 保留「第一个实验」「证据」 |

## 导航与页面

| 概念 | 中文界面 | English UI | 说明 |
|---|---|---|---|
| 工作台 | 工作台 | workbench | 产品自称；名字 PhD Studio 不翻 |
| 总览 | 总览 | Overview | |
| 阅读清单 | 阅读清单 | Reading list | 生成的 `Reading List.md` 文件名不变 |
| 时间线 | 时间线 · 会议与规划 | Timeline · venues and plan | |
| 想法 | 想法 | Ideas | ⚖️D9 |
| 方向 | 方向 · 研究线与路线图 | Direction · research lines and roadmap | |
| 追踪 | 追踪 · 同行动态与捕捉 | Track · peers and capture | ⚖️D11 |
| 论文库 | 论文库 | Library | 抽屉标题「论文」统一成「论文库」 |
| 设置 | 设置 | Settings | |
| 工作台助手 | 工作台助手 | Workbench assistant | ⌘J |
| 研究地图 | 研究地图 | Research map | #45 新增的导航项 |

## 阅读清单

| 概念 | 中文界面 | English UI | 说明 |
|---|---|---|---|
| 收件箱 | 收件箱 | Inbox | 还没定 Tier 的；⚖️D2 |
| 待读 | 待读 | Queued | T1–T3 的合称 |
| 在读 | 在读 | Reading | 打开论文时自动进入 |
| 读完 | 读完 | Done | 「已读」统一成「读完」 |
| 稍后 | 稍后 | Snoozed / Later | 按钮「稍后一周」/ Snooze a week |
| 不读 | 不读 | Dismissed / Skip | 按钮用 Skip，状态用 Dismissed |
| 超过 3 周未处理 | 超过 3 周未处理 | Untriaged for 3+ weeks | `stale` |
| Tier | T1 / T2 / T3；T1 · 本周 | T1 / T2 / T3; T1 · this week | ⚖️D1；「本周 T1」「T1 本周」统一成「T1 · 本周」 |
| 定 Tier（分拣） | 定 Tier | Triage | ⚖️D1 |
| 软上限 | 上限：T1 最多 5 篇，待读最多 12 篇 | Soft cap | `caps` |
| 加入清单 | 加入阅读清单 / 加入清单 | Add to reading list | 「推进清单」「推送」「进来」都统一成「加入」 |
| 来源 | Tracker / Claude / 导师 / 自己 | Tracker / Claude / Advisor / Me | `source` |
| 理由 | 理由 | Reason | 为什么要读的一句话 |
| 截止日期 | 截止 | Due | 阅读项自己的 due，不是会议截稿 |
| 读到什么程度 | 略读 / 精读 | Skim / Deep read | `depth` |
| 对我的价值 | 关键 / 有用 / 无关 | Key / Useful / Irrelevant | `verdict` |
| 一句话收获 | Takeaway | Takeaway | ⚖️D3；Claude 可起草 |

## 阅读器、笔记、伴读

| 概念 | 中文界面 | English UI | 说明 |
|---|---|---|---|
| 伴读 | 伴读；Claude 伴读 | Co-reader | 每篇论文一个持续会话 |
| 伴读模型 | 伴读模型 | Co-reader model | |
| 原文 / 双语 / 译文 | 原文 / 双语 / 译文 | Original / Bilingual / Translation | ⚖️D10 |
| 双语 PDF | 双语 PDF | Bilingual PDF | 「双语版」「双语版本」统一成「双语 PDF」；按钮可简写「双语」 |
| 预生成双语 PDF | 预生成双语 PDF | Make bilingual PDFs ahead | 设置项；关 / T1 / T1 + T2 |
| 双语队列 | 双语队列 | Bilingual queue | |
| 翻译 / 划词翻译 | 翻译；划词翻译 | Translate; selection translation | |
| 翻译引擎 | 翻译引擎 | Translation engine | DeepSeek / 智谱 GLM / Google 翻译；英文 Zhipu GLM / Google Translate |
| 讲解 | 讲解；讲解本页 | Explain; explain this page | |
| 问 Claude | 问 Claude | Ask Claude | |
| 摘录 | 摘录 | Excerpt | 写到笔记的某一节，默认小节 Excerpts |
| 小节 | 小节 | Section | 「这一节」统一成「小节」 |
| 批注 | 批注 | Comment | 摘录附带的一句话 |
| 框选 | 框选图表 / 公式 | Crop a figure / equation | ⌥ 拖动 |
| 目录 | 目录 | Outline | PDF 的书签 |
| 笔记 | 笔记；论文笔记 | Note; paper note | |
| 建笔记 / 更新笔记 | 建笔记 / 更新笔记 | Create note / Update note | |
| 速读 | 速读 | Quick read | 伴读快捷指令 |
| 和我的课题 | 和我的课题 | Relate to my work | 伴读快捷指令 |
| Claim Gate | Claim Gate；Claim Gate 审计 | Claim Gate; Claim Gate audit | 逐条核对 claim；笔记小节名 `## Claim Gate` 不变 |
| 页码链接 | p.5、§3.2 | p.5, §3.2 | 不翻 |

## 时间线、会议、规划

| 概念 | 中文界面 | English UI | 说明 |
|---|---|---|---|
| 会议 | 会议 | Venue | 也包括期刊 |
| 关注会议 | 关注 / 取消关注 | Watch / Unwatch | 和「关注学者」同一个中文词，英文分开 |
| 关注会议 / 全部会议 / 我的规划 | 关注会议 / 全部会议 / 我的规划 | Watched / All venues / My plan | 时间线三个标签页 |
| 截稿 | 截稿 | Deadline | ⚖️D5 |
| Abstract / Full paper 截稿 | Abstract 截稿 / Full paper 截稿 | Abstract deadline / Full paper deadline | ⚖️D5 |
| 截稿未公布 | 截稿未公布 | Deadline TBA | |
| CFP | CFP | CFP | 不翻 |
| camera-ready | camera-ready | Camera-ready | 现在界面里还没有 |
| rebuttal | rebuttal | Rebuttal | |
| 届次 | 届；届次；本届 / 上一届 / 下一届 | Edition; this / previous / next edition | 「这一届」「上届」「新一届」统一成这三个 |
| 轮次 | 轮 | Round | 一届里的某一轮截稿 |
| Track | Track；Main track | Track; Main track | ⚖️D7 |
| 投稿要求 | 投稿要求：页数、模板、审稿方式 | Submission rules: page limit, template, review model | 「审稿」「审稿模式」「评审方式」统一成「审稿方式」 |
| 录用率 | 录用率；历年录用率 | Acceptance rate | |
| CORE / CCF 等级 | CORE A*、CCF A | CORE A*, CCF A | 不翻 |
| 官网核实 / 沿用上一届 / 未核实 | 官网核实 / 沿用上一届 / 未核实 | Verified on site / From last edition / Unverified | 会议详情的数据状态 |
| 信息可能过期 | 信息可能过期 | May be out of date | |
| 让助手核对 | 让助手核对 | Ask the assistant to check | |
| 投稿目标 | 投稿目标 | Submission target | ⚖️D6 |
| 投稿状态 | 计划中 / 写作中 / 已投稿 / 已录用 / 被拒 / 放弃 | Planning / Writing / Submitted / Accepted / Rejected / Dropped | 「已投出」统一成「已投稿」 |
| 里程碑 | 里程碑 | Milestone | ⚖️D4 |
| Gate | Gate；下一个 Gate | Gate; next Gate | |
| Kill 检查点 | Kill 检查点 | Kill check | ⚖️D4；「Kill 检查」「Kill 点」统一 |
| 里程碑状态 | 待开始 / 进行中 / 完成 / 预警 | To do / In progress / Done / At risk | |
| 规划 | 规划；我的规划 | Plan; my plan | |

## 总览上的数字

| 概念 | 中文界面 | English UI | 说明 |
|---|---|---|---|
| 本周读完 | 本周读完 | Read this week | |
| 本周 T1 | T1 · 本周 n/5 | T1 · this week n/5 | |
| 待读 | 待读 n/12 | Queued n/12 | |
| 新想法 | 新想法 | New ideas | |
| 本周巡检新论文 | 本周巡检新论文 | New from this week's run | ⚖️D11（现在写「本周追踪新论文」） |
| 未来 120 天 | 未来 120 天 | Next 120 days | |
| 接下来 | 接下来 · N 件 | Coming up · N | |
| 研究节奏 | 研究节奏 | Activity | 热力图 |
| 天 / 逾期 / 未公布 | 天 / 逾期 / 未公布 | days / overdue / TBA | 倒计时环 |

## 想法

| 概念 | 中文界面 | English UI | 说明 |
|---|---|---|---|
| 想法 | 想法 | Idea | ⚖️D9 |
| 随手记 | 随手记；记一个想法 | Quick capture; jot down an idea | ⌘I |
| 想法状态 | 新 / 保留 / 已整理 / 丢弃 / 已合并 | New / Kept / Promoted / Dropped / Merged | 「留下」统一成「保留」；「成笔记」「整理想法」统一成「整理成 Idea 笔记」 |
| 漏斗 | 漏斗：记下 → 保留 → 整理成笔记 | Funnel: captured → kept → promoted | |
| 标签 | 标签 | Tag | |
| 合并 / 拆开 | 合并 / 拆开 | Merge / Unmerge | |
| Idea Radar | Idea Radar | Idea Radar | 专名 |
| 信心 | 信心 | Confidence | Idea Radar 的一列 |

## 方向：研究线与路线图

| 概念 | 中文界面 | English UI | 说明 |
|---|---|---|---|
| 研究线 | 研究线 | Research line | vault 笔记名前缀 `Research Line - ` 不变 |
| 研究线状态 | 进行中 / 暂停 | Active / Paused | |
| 优先级 | P1 / P2 | P1 / P2 | |
| 路线图 | 路线图；PhD 路线图 | Roadmap; PhD roadmap | ⚖️D8 |
| 你在这里 | 你在这里 | You are here | |
| 交付 | 交付 | Deliverable | |
| Kill criteria | Kill criteria；Kill criteria 体检 | Kill criteria; Kill criteria check-up | 统一小写 criteria；K1…Kn |
| Kill criteria 状态 | 未触发 / 部分触发 / 已触发 | Clear / Partly triggered / Triggered | |
| 第一个实验 | First Experiment | First experiment | ⚖️D17；和笔记小节 `## First Experiment` 对齐 |
| 证据 | Evidence | Evidence | ⚖️D17；vault 文件夹 Evidence |
| 导师汇报包 | 导师汇报包；起草导师汇报包 | Advisor packet; draft an advisor packet | 「汇报包」「评审材料」统一；文件夹 Review Packets 不变 |
| 研究线对话 | 和助手讨论 / 继续讨论 | Discuss with the assistant / Continue | 每条研究线一个长期对话 |
| 精读课程 | 精读课程 | Reading course | |
| baseline / ablation | baseline / ablation | Baseline / Ablation | 不翻 |
| 指标名 | Recall@10、nDCG、F1 | Recall@10, nDCG, F1 | 不翻 |

## 研究地图（#45）

| 概念 | 中文界面 | English UI | 说明 |
|---|---|---|---|
| 节点：问题 | 问题 | Question | ⚖️D14 |
| 节点：路径 / 假设 | 假设 | Hypothesis | ⚖️D14；一个节点类型只用一个词，建议「假设」 |
| 节点：子线 | 子线 | Branch | ⚖️D14 |
| 节点：实验 | 实验 | Experiment | ⚖️D14 |
| 节点：结果 | 结果 | Result | ⚖️D14 |
| 节点：决策 | 决策 | Decision | ⚖️D14 |
| 节点：Kill criteria | Kill criteria | Kill criteria | |
| 节点：开放问题 | 开放问题 | Open question | ⚖️D14 |
| 状态 | 未开始 / 进行中 / 已验证 / 没打通 / 证伪 / 放弃 | Not started / In progress / Validated / Dead end / Falsified / Abandoned | ⚖️D14；放弃和证伪变淡显示 |
| 边 | 细分 / 依赖 / 备选 / 冲突 | Refines / Depends on / Alternative to / Conflicts with | |
| 顶部指标 | 深度 / 推进中分支 / 验证比例 / 空白分支 | Depth / Active branches / Validated / Empty branches | |
| 操作 | 聚焦 / 钉住 / 折叠 | Focus / Pin / Collapse | |
| 证据 / 同行 / 结果记录 | 证据 / 同行 / 结果记录 | Evidence / Peers / Results | 节点上挂的东西 |
| 事件 | 改动记录 | Change log | 每次改动一条 |

## 追踪：同行与捕捉

| 概念 | 中文界面 | English UI | 说明 |
|---|---|---|---|
| 同行 | 同行；同行动态 | Peers; peer activity | 「同行评审」是另一个词：peer review |
| 学者动态 / 本周雷达 / 关注 / 实验室 | 学者动态 / 本周雷达 / 关注 / 实验室 | Scholars / This week's radar / Follows / Labs | 追踪页四个标签；⚖️D11 |
| 巡检 | 巡检；立即巡检；巡检日志 | Run; run now; run log | ⚖️D11；每周一次的 tracker 运行 |
| Tracker | Tracker | Tracker | 阅读清单来源、Lab Tracker 专名 |
| Tier A / B / C | A 必读 / B 相关 / C 领域内 | A must-read / B relevant / C in the field | 巡检给论文的档，和阅读 T1–T3 不同 |
| 主题门槛 | 未过主题门槛 | Below the topic bar | 不叫 Gate，避免和里程碑 Gate 混 |
| 本周分析 | 本周分析；完整周报 | This week's analysis; full report | |
| 关注（关键词 / 学者） | 关注；关注关键词 / 关注学者 | Follow; follow a keyword / a scholar | |
| 学者 | 学者；学者名单 | Scholar; scholar list | |
| 名单更新建议 | 名单更新建议；应用到名单 / 忽略 | Suggested list update; apply / dismiss | |
| 固定 / 排除 | 固定 / 排除 | Pin / Exclude | |
| 实验室 | 实验室；学界 / 业界 | Lab; academia / industry | ⚖️D12 |
| 跨实验室动态 | 跨实验室动态 | Activity across labs | 热力图 |
| 触及我的课题 | 触及我的课题 | Touches my work | |
| 捕捉 | 捕捉；捕捉论文 | Capture; capture a paper | 输入标题或链接直接进收件箱 |
| 引用指标 | 被引；h-index | Citations; h-index | ⚖️D13 |
| 今年论文 / 近 30 天新论文 | 今年论文 / 近 30 天新论文 | Papers this year / New in 30 days | |

## 助手与模型

| 概念 | 中文界面 | English UI | 说明 |
|---|---|---|---|
| 差异 / 允许写入 / 不要改 | 差异；允许写入 / 不要改 | Diff; allow write / don't change | 改 vault 前的确认卡片 |
| 已写入 vault / 已拒绝 | 已写入 vault / 已拒绝 | Written to the vault / Declined | |
| 新对话 / 新线程 | 新对话；新线程 | New chat; new thread | 换模型来源时开新线程 |
| 模型来源 | 模型来源 | Provider | 「来源」单用时容易和论文来源混，带上「模型」 |
| 订阅登录 / API Key | 订阅登录 / API Key | Subscription / API key | |
| 只读 | 只读 | Read-only | 非 Claude 模型 |
| 看图 | 看图 | Images | 模型能看图 |
| 回答语言 | 回答语言 | Answer language | 设置项，和界面语言分开 |

## 提醒、Zotero、设置

| 概念 | 中文界面 | English UI | 说明 |
|---|---|---|---|
| 截稿提醒 | 截稿提醒 | Deadline reminders | |
| 提前量 | 提前量 | Lead times | 当天 / N 天 |
| 免打扰 | 免打扰 | Quiet hours | |
| 提醒设备 | 提醒设备；本机 | Sending Mac; this Mac | 两台 Mac 只由一台发 |
| 后台检查 | 后台检查 | Background check | LaunchAgent |
| 入库 | 加入 Zotero；自动入库 | Add to Zotero; add automatically | |
| 待写回的链接 | 待写回的链接 | Links to write back | Zotero 链接写回笔记 |
| 分类 | 分类；库的根目录 | Collection; library root | Zotero 的 collection |
| 界面语言 | 界面语言：跟随系统 / 中文 / English | Interface language: follow system / 中文 / English | 语言名两种界面都写本名 |

## 要统一的写法

扫描现有界面时发现的不一致，抽取时顺手统一（括号里是文件位置的例子）：

1. 读完 vs 已读（`track.js`）→ 读完。
2. T1 · 本周 vs T1 本周 vs 本周 T1（`reading.js`、`plan.js`）→ T1 · 本周。
3. 加入清单 / 推进清单 / 推送 / 进来 → 加入阅读清单（按钮可写「清单」）。
4. 投稿目标 vs 目标 vs 我的目标（`plan.js`）→ 投稿目标。
5. Kill 检查 vs Kill 点 vs Kill 检查点；Kill Criteria vs Kill criteria → Kill 检查点、Kill criteria。
6. 截稿 vs 截止 vs 到期：会议用截稿，阅读项和里程碑用截止，提醒文案「今天到期」改「今天截止」。
7. 届 / 届次 / 这一届 / 上届 / 新一届 → 届次、本届、上一届、下一届。已投出 → 已投稿。
8. 双语 / 双语版 / 双语版本 → 双语 PDF（按钮「双语」）。
9. 巡检 / 追踪 / 雷达；追踪 vs 跟踪 → 见 D11。关注同时指会议和学者：中文不变，英文分 Watch / Follow。
10. 实验室 vs lab → 见 D12。被引 / 引用 / 总引用；h 指数 vs h → 见 D13。
11. 已整理 / 整理成笔记 / 成笔记；保留 vs 留下 → 整理成 Idea 笔记、保留。
12. 导师汇报包 / 汇报包 / 评审材料 → 导师汇报包。
13. 审稿 / 审稿模式 / 评审方式 → 审稿方式。
14. 论文库 vs 论文（抽屉标题）→ 论文库。小节 vs 节 → 小节。
15. 同一个词指两件事：「立即更新」（会议数据、学者名单）→ 「更新会议数据」「重算名单」；「Gate」只指里程碑 Gate（Claim Gate 是专名，巡检的叫「主题门槛」）；「Tier」：阅读 T1–T3 和巡检 A/B/C 分开写；「来源」→「模型来源」；「方向」只指方向页，实验室的叫「研究方向」。
