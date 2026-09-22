# 柑橘合格证追溯系统 · 前端（Vue3 + Vite + Element Plus）

## 技术栈

Vue 3 + Vite + Element Plus + Pinia + Vue Router + Axios + ECharts

## 启动

```bash
# 安装依赖（推荐用国内源）
npm install --registry=https://registry.npmmirror.com

# 启动开发服务器
npm run dev
```

前端地址：http://localhost:5173

已配置代理：`/api` → `http://127.0.0.1:8000`（后端需先启动）

## 构建

```bash
npm run build      # 产物在 dist/
npm run preview    # 本地预览构建产物
```

## 页面结构

| 路由 | 页面 | 说明 |
| --- | --- | --- |
| `/login` | 登录 | 四类角色演示账号一键填充 |
| `/verify/:certNo?` | 公众验真 | 免登录，扫码入口，展示存证与哈希比对 |
| `/dashboard` | 数据看板 | 统计卡片 + ECharts 图表 |
| `/producer` | 生产主体 | 备案、审核（通过/驳回） |
| `/batch` | 生产批次 | 建档、批次存证 |
| `/report` | 检测报告 | 上传报告、自动存证 |
| `/certificate` | 合格证管理 | 签发、二维码、作废 |
| `/evidence` | 存证与核验 | 存证记录列表 + 哈希核验 |
| `/supply` | 产销对接 | 供应/求购发布、先验真再对接 |

## 部署

```bash
npm run build
# 将 dist/ 交给 Nginx 静态托管，并反代 /api 到后端
```

Nginx 参考配置：

```nginx
server {
    listen 80;
    root /var/www/citrus-trace/dist;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }
    location /api/ {
        proxy_pass http://127.0.0.1:8000/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```