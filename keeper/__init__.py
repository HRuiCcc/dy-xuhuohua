"""火花管家 dy-xuhuohua —— 抖音续火花系统（自主实现）。

模块一览：
- settings  环境与账号配置读写
- logger    环形内存日志 + 文件日志
- store     JSON 文件原子读写
- auth      Web 控制台令牌鉴权
- accounts  多账号注册表与数据目录隔离
- browser   Playwright 浏览器会话池（并发限流 + 反自动化指纹）
- login     扫码登录会话管理（QR 生成 / 轮询 / 落盘）
- douyin    抖音网页版操作：登录态检查 / 火花好友同步 / 发送消息
- runtime   全局任务锁与运行模式（正式 / 演练 / 仅同步）
- scheduler APScheduler 定时任务（每日续火花 + 每周维护）
"""

__version__ = "1.0.0"
__app_name__ = "火花管家 dy-xuhuohua"
