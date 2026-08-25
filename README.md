# dy-xuhuohua（火花管家）🔥

抖音自动续火花系统（自主实现）。用一台常开的电脑或云服务器，每天在你设定的时间自动给选中的火花好友发一条消息，把「火花 · 连续聊天天数」一直养下去——不用每天定闹钟手动发。

## 为什么需要它

抖音的「火花」是朋友之间连续互发消息累积的天数徽章，**任何一方断一天，火花就会熄灭、天数清零**。养了上百天的火花突然没了，往往是忘了发消息、忙到深夜才发现、或者出门在外没信号。

本项目的解法很朴素：**把「记得发消息」这件事交给程序**。你只需要：

1. 扫码登录一次（凭证保存在你自己的机器上）
2. 勾选要维护的火花好友
3. 设置好每天发送的时间与文案

之后每一天，系统都会在设定时间自动、随机浮动地向选中的好友发送一条消息，替你保住每一颗火花。关掉电脑、断网、手机没电都不影响——只要服务在跑，火花就在。

## 界面截图

**概览**

![概览](docs/screenshots/overview.png)

**火花好友**

![好友](docs/screenshots/friends.png)

**定时与发送**

![定时](docs/screenshots/schedule.png)

## 功能特性

### 1. 图形化网页控制台

原生 JS 实现的单页控制台（无任何前端框架依赖），暗色暖焰主题，六大模块：

| 模块 | 能力 |
|---|---|
| 📊 概览 | 账号数、登录态、任务状态、各账号下次执行时间一览 |
| 👥 账号 | 多账号增删改查、启停、切换 |
| 📱 凭证 | 扫码登录、登录态查看、二维码实时展示 |
| 💬 好友 | 同步联系人、火花天数展示、勾选管理 |
| ⏰ 定时 | 发送时间、随机浮动、发送间隔、文案池配置 |
| 📜 日志 | 最近 600 行运行日志实时查看、自动刷新 |

### 2. 手机扫码登录

- 控制台点击「扫码登录」后，服务端在后台驱动真实 Chromium 打开抖音登录页，把二维码提取成图片回传网页
- 用抖音 App 扫码即可登录，**全程不需要手动提取 Cookie**
- 登录态以 Playwright storage_state 形式保存在本地磁盘，重启不丢
- 触发二次安全验证（如刷脸）时自动引导，按提示在手机上完成即可

### 3. 火花好友管理

- 一键「同步联系人」：自动从抖音聊天页拉取好友列表与当前火花天数
- 好友按火花天数从高到低排列，谁的火花最值钱一目了然
- 「一键勾选火花好友」自动选中所有带火花的好友，也可手动增删
- 勾选结果持久化，同步后按昵称自动匹配、保留原有选择

### 4. 智能定时发送

- **定时 + 随机浮动**：设置 21:00、浮动 30 分钟，系统会在 20:30~21:30 之间随机挑一个时间发送——每天时间不一样，模拟真人行为
- **文案池随机**：多条文案里随机挑一条，避免每天发一模一样的话
- **随机发送间隔**：多人发送时每人间隔 6~12 分钟随机停顿，不瞬间刷屏
- **首条消息限额**：无火花好友是否发送、每天最多发几条，都可控
- 修改配置**实时生效**，无需重启服务

### 5. 三种运行模式

| 模式 | 行为 | 适用场景 |
|---|---|---|
| 🧪 模拟演练 | 走完整流程但不真正发送 | 部署后第一次试跑 |
| 🚀 正式发送 | 真实发送消息 | 日常自动任务 |
| 🔄 仅同步好友 | 只刷新好友列表 | 添加新好友后 |

### 6. 多账号隔离

一个服务可以同时管理多个抖音账号：

- 每个账号独立数据目录（登录态 / 好友列表 / 配置互不干扰）
- 每个账号独立定时任务，可以设不同时间
- 全局最多同时开 5 个浏览器会话，超出自动排队，防风控
- 删除账号时数据自动归档到 `data/_archived/`，不误删

### 7. 风控对抗设计

针对抖音的自动化检测，内置多层对抗：

- **真实浏览器内核**：Playwright 驱动的 Chromium，非 HTTP 模拟
- **指纹伪装**：playwright-stealth 注入 + WebGL 厂商伪装（Intel）
- **UA 一致性**：User-Agent 跟随真实内核版本号，不写死
- **行为拟人**：随机浮动时间、随机发送间隔、随机文案
- **并发限流**：浏览器会话池上限 5，防止多账号同开触发风控

## 核心设计

### 架构

```
┌─────────────────────────────────────────────┐
│                 网页控制台（static/）          │
│   概览 · 账号 · 凭证 · 好友 · 定时 · 日志      │
└──────────────────┬──────────────────────────┘
                   │ REST API（X-Auth-Token）
┌──────────────────▼──────────────────────────┐
│                app.py（FastAPI）             │
│   账号 / 登录 / 好友 / 配置 / 运行 / 日志      │
└──┬───────────┬───────────┬───────────┬──────┘
   │           │           │           │
┌──▼────┐ ┌────▼────┐ ┌────▼────┐ ┌────▼──────┐
│login.py│ │douyin.py│ │runtime.py│ │scheduler.py│
│扫码会话 │ │抖音操作  │ │任务编排  │ │定时调度    │
└──┬────┘ └────┬────┘ └────┬────┘ └────┬──────┘
   │           │           │           │
   └───────────┴─────┬─────┴───────────┘
                     │
              ┌──────▼──────┐
              │ browser.py   │  Chromium 会话池
              │ （限流+指纹） │
              └─────────────┘
```

### 登录会话状态机

```
queuing ──→ starting ──→ waiting_scan ──→ success
   │            │              │
   └────────────┴──────────────┴──→ expired / failed / cancelled
```

- 二维码过期自动检测、自动刷新（上限 5 次）
- 登录成功判定覆盖 5 种 Cookie 变体（sessionid / sessionid_ss / sid_tt / sid_guard / uid_tt）
- 会话硬超时看门狗（390 秒）防止线程卡死泄漏浏览器
- 同一账号同时只允许一个扫码会话，重复点击幂等返回当前会话

### 调度机制

不使用 cron 表达式，而是「执行一次、排下一次」：

1. 每次执行完成后，取配置的 `schedule_time`，叠加 ±`jitter_minutes` 的随机偏移
2. 若目标时间已过今天，自动顺延到明天
3. 每个账号独立注册 APScheduler 任务
4. 每周一 03:00 自动执行维护任务（清理过期会话）

这样每天的实际发送时间都不同，且配置修改后下一次调度立即生效。

## 快速开始

### 环境要求

- Python 3.10+
- 可访问抖音的网络环境
- 一台能长期开机的电脑（家用旧电脑 / NAS / 云服务器均可）

### 安装步骤

```bash
# 1. 克隆项目
git clone https://github.com/HRuiCcc/dy-xuhuohua.git
cd dy-xuhuohua

# 2. 创建虚拟环境并安装依赖
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. 安装浏览器内核（Chromium，约 150MB，只需一次）
playwright install chromium

# 4. 生成配置
cp .env.example .env               # 编辑 AUTH_TOKEN，改成随机长字符串
cp config.example.json config.json
```

### 启动

```bash
python app.py
# 默认监听 http://0.0.0.0:8020
```

浏览器打开 `http://127.0.0.1:8020`（局域网内其他设备可用 `http://本机IP:8020`），输入 `.env` 中的 `AUTH_TOKEN` 进入控制台。

## 使用教程

### 第一步：扫码登录

1. 左侧进入「📱 凭证」页
2. 点击「📱 手机扫码登录」
3. 约 20~40 秒后页面出现二维码，用抖音 App 扫码
4. 若触发二次验证（刷脸等），按页面提示用手机完成
5. 页面提示「登录成功」即完成，凭证已保存在本地

### 第二步：同步并勾选好友

1. 进入「💬 好友」页，点击「🚀 同步联系人」（约 10~30 秒）
2. 列表按火花天数降序排列，点击「✨ 一键勾选火花好友」
3. 也可以手动勾选/取消个别好友
4. 点击「💾 保存勾选」生效

### 第三步：设置定时

1. 进入「⏰ 定时」页
2. 设置发送时间（默认 21:00）与随机浮动（默认 ±30 分钟）
3. 设置发送间隔（默认 6~12 分钟随机）与单次人数上限
4. 在文案框里每行写一条文案，系统随机挑一条发送
5. 点击「保存设置」，实时生效

### 第四步：试跑验证

进入「📊 概览」页，点「🧪 模拟演练」先跑一遍——系统会走完整流程并输出日志，但不会真正发消息。确认无误后，等定时任务自动执行，或点「🚀 正式发送」立即跑一次。

## 云服务器部署（可选）

想要 24 小时在线不依赖家里电脑，可以部署到云服务器（**强烈建议选择国内、同城节点**，登录 IP 与日常城市一致，风控概率最低）：

```bash
# 服务器上执行（Ubuntu 22.04/24.04 为例）
sudo apt update && sudo apt install -y python3-venv python3-pip
git clone https://github.com/HRuiCcc/dy-xuhuohua.git
cd dy-xuhuohua
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install --with-deps chromium
cp .env.example .env && vim .env    # 改 AUTH_TOKEN
```

注册 systemd 开机自启：

```ini
# /etc/systemd/system/dy-xuhuohua.service
[Unit]
Description=dy-xuhuohua spark keeper
After=network.target

[Service]
WorkingDirectory=/opt/dy-xuhuohua
ExecStart=/opt/dy-xuhuohua/.venv/bin/python app.py
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now dy-xuhuohua
```

别忘了在云控制台安全组放行 **8020 端口（TCP）**。

## 配置说明

`.env` 环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| PORT | 8020 | Web 服务端口 |
| HOST | 0.0.0.0 | 监听地址（仅本机访问可改 127.0.0.1） |
| AUTH_TOKEN | — | 控制台访问令牌，**务必修改** |
| TZ | Asia/Shanghai | 时区 |
| DATA_DIR | ./data | 数据目录 |
| MAX_BROWSERS | 5 | 浏览器会话并发上限 |

`config.json`（也可在网页「定时」页修改，每个账号一份）：

| 字段 | 默认 | 说明 |
|---|---|---|
| schedule_time | 21:00 | 每日发送时间 |
| jitter_minutes | 30 | 随机浮动 ±N 分钟 |
| send_gap_min / send_gap_max | 6 / 12 | 发送间隔随机区间（分钟） |
| max_friends_per_run | 20 | 单次最多发送人数 |
| messages | 🔥 续火花 等 | 文案池（随机挑一条） |
| auto_run_enabled | true | 是否启用定时任务 |
| allow_first_message | false | 是否允许给无火花好友发首条 |
| first_message_daily_limit | 1 | 首条消息每日限额 |

## 目录结构

```
dy-xuhuohua/
├── app.py               # FastAPI 入口与全部 API 路由
├── requirements.txt
├── .env.example         # 环境变量样例
├── config.example.json  # 任务配置样例
├── keeper/              # 核心引擎（全部自主实现）
│   ├── settings.py      # 环境变量 / 账号配置读写
│   ├── logger.py        # 环形内存日志（网页可读）+ 文件轮转
│   ├── store.py         # JSON 原子读写（防写入中断）
│   ├── auth.py          # 令牌常数时间鉴权
│   ├── accounts.py      # 多账号注册表与数据隔离
│   ├── browser.py       # Chromium 会话池（限流 + 指纹伪装）
│   ├── login.py         # 扫码登录会话状态机
│   ├── douyin.py        # 抖音操作层（好友同步 / 发消息）
│   ├── runtime.py       # 任务锁 + 三种运行模式 + 限额台账
│   └── scheduler.py     # APScheduler 调度（每日 + 每周维护）
├── static/              # 网页控制台（原生 JS，零框架）
├── docs/screenshots/    # 界面截图
└── data/                # 运行数据（.gitignore 排除，勿外传）
    ├── accounts/{id}/   # 每账号：state.json / friends.json / config.json
    └── logs/            # 运行日志
```

## API 文档

所有接口需要请求头 `X-Auth-Token: <令牌>`，多账号场景用 `X-Account: <账号id>` 指定上下文（默认 `default`）。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /api/auth/check | 校验令牌 |
| GET | /api/status | 系统状态 + 定时快照 |
| GET / POST | /api/accounts | 账号列表 / 新建 |
| POST | /api/accounts/{id}/rename | 账号改名 |
| POST | /api/accounts/{id}/toggle | 账号启停 |
| DELETE | /api/accounts/{id} | 删除账号（数据归档） |
| POST | /api/login/start | 发起扫码登录（幂等） |
| GET | /api/login/status | 轮询：queuing/starting/waiting_scan/success/expired/failed/cancelled |
| POST | /api/login/cancel | 取消扫码会话 |
| GET | /api/friends | 好友列表 |
| POST | /api/friends/sync | 同步联系人（异步任务） |
| POST | /api/friends/select | 勾选/取消勾选 `{names, selected}` |
| POST | /api/friends/auto-select | 一键勾选火花好友 |
| GET / PUT | /api/config | 读取 / 修改定时配置 |
| POST | /api/run | 立即执行 `{mode: send/dry/sync}` |
| POST | /api/stop | 强制停止当前任务 |
| GET | /api/logs?lines=100 | 最近日志 |

curl 示例：

```bash
TOKEN="你的令牌"

# 查看状态
curl -H "X-Auth-Token: $TOKEN" http://127.0.0.1:8020/api/status

# 发起扫码登录
curl -X POST -H "X-Auth-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{}' http://127.0.0.1:8020/api/login/start

# 修改发送时间为 22:30、浮动 15 分钟
curl -X PUT -H "X-Auth-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{"schedule_time":"22:30","jitter_minutes":15}' http://127.0.0.1:8020/api/config
```

## 常见问题

**Q1：扫码后二维码一直不出来？**
二维码生成约需 20~40 秒（首次冷启动更慢），耐心等待并留意页面提示；超过 1 分钟仍无码，点「取消登录」后重新发起；多次失败可在「日志」页查看具体报错。

**Q2：扫码后一直转圈不成功？**
大概率触发了抖音二次安全验证：按页面提示扫描新二维码、在手机上完成刷脸/确认即可。仍未解决可取消后等 1 分钟重试。

**Q3：同步联系人结果为 0？**
先确认「凭证」页显示已登录；若已登录仍为 0，可能是抖音页面改版导致解析失效，请把「日志」页的「页面诊断片段」反馈给开发者。

**Q4：登录态多久过期？**
抖音网页登录态一般维持几天到几周。控制台顶部显示「未登录」或发送失败提示登录失效时，重新扫码即可，无需重启或重新部署。

**Q5：能不能发图片/表情？**
当前版本只支持文本消息（文案池支持 emoji）。图片发送在路线图中。

**Q6：数据存在哪里？安全吗？**
全部数据（登录态、好友列表、配置）只保存在你自己的 `data/` 目录，绝不上传任何第三方。该目录已在 `.gitignore` 中排除，不会随代码提交泄露。

**Q7：每天发送时间可以完全固定吗？**
可以。把「随机浮动」设为 0 即严格固定时间；但保留少量浮动更像真人行为，建议至少 ±5 分钟。

## 路线图

- [ ] 消息模板变量（好友昵称、日期、随机 emoji 组合）
- [ ] 图片 / 表情包发送
- [ ] 火花熄灭预警（火花天数骤降提醒）
- [ ] Docker 一键部署
- [ ] 多服务器同步（登录态云备份）
- [ ] 网页端数据导出（好友清单 CSV）

## 免责声明

1. 本项目仅供个人学习、自动化测试与亲友间互动交流使用
2. 请合理设置发送频率与好友数量，切勿用于商业营销、批量骚扰等违规用途
3. 自动化操作可能违反抖音平台规则，建议先用小号试跑
4. 使用本项目所产生的一切账号与法律后果均由使用者自行承担

## License

[MIT](LICENSE)

功能需求参考社区开源项目 douyin-cloud-streak（MIT）；本项目的架构与全部代码均为独立实现。
