# 前端组件名称对照表

本项目 Web 界面（`web/templates/index.html`）各部分的描述用语与标准前端组件名称对照，便于沟通时统一口径。

| 通俗描述 | 标准名称 | 英文/行业叫法 | 对应代码位置（class/id） |
|---|---|---|---|
| 左侧 task 栏 | 侧边栏 | **Sidebar** | `.sidebar` |
| 侧栏里的任务条目 | 任务卡片 | **Task Card** / List Item | `.task-item` |
| 全部/待办/进行中/已完成 | **Filter Pills** | 胶囊筛选器 / Segmented Filter | `.filter-btn` |
| 底部「+ 添加任务」 | 底部操作按钮 | **CTA Button**（Call To Action） | `.add-task-btn` |
| 顶部一排日期 | 日期导航条 | **Date Strip** / Date Navigator | `.date-nav` / `.date-item` |
| 右侧 0–24 点格子 | 时间轴 / 时间网格 | **Timeline** / Time Grid | `.timeline-grid` `#timeline` |
| 时间轴上的任务色块 | 日程块 | **Event Block** / Time Block | `.hour-task` |
| 橙色当前时间线 | 当前时间指示线 | **Now Indicator** | `.current-time-line` |
| 未设置时间的任务区 | 待排期区 | **Unscheduled** / Backlog Section | `.untasks-section` |
| 凌晨时段折叠栏 | 折叠面板 | **Collapsible Section** / Disclosure | `.early-hours-toggle` / `#earlyHours` |
| 添加/编辑任务弹窗 | 模态框 | **Modal** / Dialog | `.modal-overlay` / `#addModal` / `#editModal` |
| 时间滚筒 | 滚筒选择器 | **Wheel Picker**（iOS 风格） | `.wheel-picker` / `.wheel-col` |
| 优先级高/中/低按钮组 | 分段控件 | **Segmented Control** | `.priority-segment` / `.priority-opt` |
| 标签小胶囊 | 标签片 | **Chips** / Tag Pills | `.tag-option` |
| 拖拽时的提示线 | 拖放指示线 | **Drop Indicator** | `.drag-guide-line` |
| 任务名称等文本框 | 输入框 | **Text Field** / Input | `.form-group input[type="text"]` |
| 表单标题（加粗） | 表单标签 | **Form Label** | `.form-group label` |
| 窄屏滑出的侧栏 | 抽屉 | **Drawer** | `.sidebar.open`（≤800px） |
| 侧栏收起/展开按钮 | 侧栏开关 | **Sidebar Toggle** | `.sidebar-collapse-btn` / `.sidebar-expand-fab` |
| Windows 右下角通知 | 系统通知 | **Toast Notification** | `todo/reminder.py`（win10toast） |

## 配色约定

- **棕色** `--accent`（#8b6f47）：交互与选中态（选中的日期、筛选器、滚筒高亮带等）
- **橙色** `--current-time`（#c17f3e）：时间指示（当前时间线）
- **优先级色**：高 `#dc5040`（红）/ 中 `#d2963c`（琥珀）/ 低 `#5aaa64`（绿）
