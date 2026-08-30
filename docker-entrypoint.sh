#!/usr/bin/env bash
# 容器入口：对齐原 README 的「生成配置」步骤
set -e

# 缺少配置文件时从样例拷贝（仅在目标不存在时执行，避免覆盖用户已修改的文件）
if [ ! -f /app/.env ] && [ -f /app/.env.example ]; then
    cp /app/.env.example /app/.env
    echo "[entrypoint] 已根据 .env.example 生成 /app/.env（容器内通过环境变量覆盖 AUTH_TOKEN）"
fi

if [ ! -f /app/config.json ] && [ -f /app/config.example.json ]; then
    cp /app/config.example.json /app/config.json
    echo "[entrypoint] 已根据 config.example.json 生成 /app/config.json"
fi

# 执行传入的命令（默认 python app.py）
exec "$@"
