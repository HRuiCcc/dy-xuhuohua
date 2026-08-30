# dy-xuhuohua（火花管家）Docker 镜像
# 复刻原 README 的安装流程：venv/pip install -> playwright install chromium
# 这里用 Python 官方镜像 + 容器内 pip/playwright 安装，等价于原本地步骤。
#
# 想跳过「构建时下载 Chromium」、进一步加快构建，可改用预装浏览器的底包：
#   FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy
# 并把下方 `pip install` 与 `playwright install --with-deps chromium` 两步去掉即可。

FROM 127.0.0.1:5000/python:3.11-slim-bookworm

# 时区 + 无缓冲日志输出（容器日志实时可见）
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Shanghai

WORKDIR /app

# 系统基础依赖；Playwright 的 --with-deps 会再补装 Chromium 运行库
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg tzdata \
    && rm -rf /var/lib/apt/lists/*

# 先拷贝依赖清单，最大化利用 Docker 层缓存
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt \
    # 安装 Chromium 及其系统依赖（对应原流程的 `playwright install chromium`） 
    && playwright install --with-deps chromium \
    # 清理安装缓存，减小镜像体积
    && rm -rf /root/.cache/ms-playwright/.links

# 拷贝全部源码（受 .dockerignore 约束）
COPY . .

# 运行数据持久化目录（挂载到宿主机，登录态/好友/配置重启不丢）
VOLUME ["/app/data"]
EXPOSE 8020

# 入口脚本：缺少 .env / config.json 时从样例自动生成（保持与原流程一致）
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "app.py"]
