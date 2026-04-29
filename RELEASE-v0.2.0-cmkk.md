# Release v0.2.0-cmkk

> Fork 自 [KylinMountain/TradingAgents-AShare](https://github.com/KylinMountain/TradingAgents-AShare)  
> 适配私有化部署，修复生产环境关键问题。

## 🐛 Bug 修复

### 前端 API 地址硬编码（已修复）
- **问题**：前端构建时硬编码 `http://localhost:8000`，导致生产环境（内网 IP / 外网域名 / 反代）无法正常使用
- **修复**：移除 `frontend/.env.local`，修改 `getBaseUrl()` 逻辑，改为跟随 `window.location.origin`
- **效果**：内网 IP、外网域名、1Panel 反代、腾讯云加速均可正常访问，无需重新构建

### 智能分析超时限制过短（已修复）
- **问题**：默认 `TA_JOB_TIMEOUT=600`（10 分钟），但 14 Agent 多轮辩论分析普遍需要 15 分钟，导致前端报"分析失败"
- **修复**：默认超时改为 `1800` 秒（30 分钟），可通过环境变量 `TA_JOB_TIMEOUT` 自定义
- **效果**：分析流程不再因超时中断，体验更流畅

## 📦 部署说明

### Docker 一键部署
```bash
docker pull ghcr.io/kylinmountain/tradingagents-ashare:latest

mkdir -p $(pwd)/data
export TA_APP_SECRET_KEY=$(openssl rand -base64 32)

docker run -d -p 8000:8000 \
  --name tradingagents \
  -v $(pwd)/data:/app/data \
  -e DATABASE_URL="sqlite:///./data/tradingagents.db" \
  -e TA_APP_SECRET_KEY="${TA_APP_SECRET_KEY}" \
  -e TA_JOB_TIMEOUT=1800 \
  ghcr.io/kylinmountain/tradingagents-ashare:latest
```

### 1Panel / Docker Compose 部署
```yaml
services:
  tradingagents:
    image: ghcr.io/kylinmountain/tradingagents-ashare:latest
    container_name: tradingagents
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=sqlite:///./data/tradingagents.db
      - TA_APP_SECRET_KEY=你的密钥
      - TA_JOB_TIMEOUT=1800
      - APP_ENV=production
      - CORS_ALLOW_ORIGINS=https://你的域名.com
    volumes:
      - ./data:/app/data
```

## 🔧 环境变量说明

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `TA_APP_SECRET_KEY` | 内置默认密钥 | 加密用户 API Key 和签发 JWT，生产环境务必设置 |
| `TA_JOB_TIMEOUT` | `1800` | 分析任务超时时间（秒），默认 30 分钟 |
| `APP_ENV` | `development` | 运行环境，生产环境设为 `production` |
| `CORS_ALLOW_ORIGINS` | 仅允许 localhost | 允许访问的域名列表，逗号分隔 |
| `MAIL_HOST` | 无 | SMTP 服务器地址，用于验证码邮件发送 |
| `MAIL_PORT` | `587` | SMTP 端口 |
| `MAIL_USER` | 无 | 发件邮箱 |
| `MAIL_PASS` | 无 | 邮箱授权码 |
| `MAIL_SSL` | `0` | 是否启用 SSL（1=启用，0=禁用） |

## ⚠️ 注意事项

1. **`TA_APP_SECRET_KEY` 首次设置后不可更改**，否则已加密的用户 API Key 将无法解密
2. **前端 API 地址已改为动态获取**，无需再配置 `VITE_API_URL`，访问地址即为 API 地址
3. **分析超时已调整为 30 分钟**，如果你的模型响应较慢，可适当调大 `TA_JOB_TIMEOUT`
4. **邮件验证码**：需配置 SMTP 才能正常发送，否则验证码会打印在容器日志中

## 📝 变更日志

- `c41e9cd` fix: increase default job timeout to 30 minutes (1800s)
- `16b7f1c` fix: use current origin for frontend API base URL

## 🙏 致谢

本项目核心架构灵感与部分基础逻辑源自 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) (Apache 2.0)。
