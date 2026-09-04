# scnet-token-plan-usage

[English](README_EN.md) | 中文

**超算互联网（scnet.cn）Token Plan 用量实时监控 —— Hermes Agent 桌面插件**

在 Hermes 桌面应用里实时查看 Token Plan 的 Credits 消耗：本周期已用/剩余、今日消耗、按模型明细、按日趋势——不用再打开控制台网页。

## 为什么需要它

SCNet 不提供任何可编程的用量查询接口（控制台页面是会话 cookie 鉴权，`sk-tp-` API Key 只能调用推理端点）。本插件换一条路：**Hermes 本身就把每次 API 调用的 token 记账进了本地 `state.db`**，插件聚合这些记录，按官方计费公式折算成 Credits。

## 计费公式

```
credits = 模型综合扣减倍率 × (未缓存输入/120 + 缓存命中/2000 + 输出/28.33)
```

- 单价锚点来自官方公开的 Kimi-K2.6 数据（60,000 Credits ≈ 720 万未缓存输入 / 1.2 亿缓存输入 / 170 万输出，对应 1.00× 模型）
- 各模型倍率内置 2026-09-01 官方值（GLM-5.3=2.29、Kimi-K3=4.12、GLM-5.3-Flash=0.15 等），**官方按周调整**，插件设置里可手动修正
- Hermes 记账的 `input_tokens` 已扣除缓存命中部分，与计费口径天然一致

## 安装

```bash
git clone https://github.com/osuCarl/scnet-token-plan-usage.git
cd scnet-token-plan-usage
./install.sh          # Windows Git Bash / macOS / Linux
# 或手动：把整个目录复制到 ~/.hermes/plugins/scnet-usage/（Windows: %HERMES_HOME%\plugins\）
```

然后：

1. 启用插件：
   ```bash
   hermes plugins enable scnet-usage
   ```
2. 重启 Hermes 桌面应用（后端路由在进程启动时挂载）
3. Settings → Plugins → 打开「SCNet Usage Monitor」开关
4. 状态栏会出现用量 chip；点击或 ⌘K「SCNet：打开用量面板」打开完整面板
5. 在面板右上角齿轮里设置套餐档位（基础/标准/高级/旗舰版）和购买日，即可看到剩余额度与进度条

## 功能

- **状态栏 chip**：常驻显示剩余 Credits，低于 25% 转黄、低于 10% 转红
- **用量面板**：本周期/今日 Credits、进度条、每日消耗柱状图、按模型明细（倍率、tokens、调用数）
- **每 30 秒自动刷新**（React Query 轮询，⌘K 里有手动刷新命令）
- **可配置**：套餐额度、周期起始日、单模型倍率覆盖——都存在本地 `config.json`

## 局限（重要）

- 只统计 **Hermes 发起的调用**。同一把 Token Plan Key 在其他工具（Cursor、Claude Code、Cline 等）产生的消耗不在此数据里——对账时请以控制台「Token 用量」页为准
- 估算是公式折算，倍率官方每周调整，数字可能与控制台有少量偏差；偏差大时在设置里修正对应模型的倍率即可

## 数据与隐私

- 全部数据来自本地 `state.db`（只读 SQLite 查询），不向任何服务器发送请求
- 不读取、不存储、不传输你的 API Key

## 文件结构

```
scnet-token-plan-usage/
├── plugin.yaml              # 插件清单
├── __init__.py              # Python 包标记
├── dashboard/
│   ├── manifest.json        # dashboard 后端清单（/api/plugins/scnet-usage）
│   └── plugin_api.py        # FastAPI 后端：SQL 聚合 + Credits 折算
├── desktop/
│   └── plugin.js            # 桌面 UI：面板 + 状态栏 chip + ⌘K 命令
├── install.sh               # 一键安装脚本
└── README.md / README_EN.md
```

## License

MIT
