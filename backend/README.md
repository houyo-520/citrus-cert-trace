# 柑橘合格证追溯系统 · 后端（Django + DRF）

基于 Django 的柑橘合格证可信存证与追溯系统 —— 后端服务。

技术栈：Django 4.2 + DRF + SimpleJWT + MySQL/SQLite + qrcode + hashlib(SHA-256)

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

或指定国内源：

```bash
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
```

### 2. 数据库配置

默认使用 SQLite（零配置，方便演示）。如需 MySQL，见 `config/settings.py` 中的 `DATABASES` 注释。

### 3. 初始化数据库

```bash
python manage.py makemigrations
python manage.py migrate
python manage.py init_data      # 初始化角色、演示账号、示例数据
```

### 4. 启动

```bash
python manage.py runserver 0.0.0.0:8000
```

后端地址：http://localhost:8000
API 文档/根路径：http://localhost:8000/api/

## 默认账号

| 账号 | 密码 | 角色 |
| --- | --- | --- |
| admin | admin123 | 系统管理员 |
| regulator | admin123 | 监管人员 |
| producer | admin123 | 种植主体 |
| buyer | admin123 | 收购商 |

## 核心 API

| 接口 | 方法 | 说明 |
| --- | --- | --- |
| `/api/auth/login/` | POST | 登录获取 JWT |
| `/api/auth/refresh/` | POST | 刷新 token |
| `/api/producer/` | CRUD | 经营主体备案 |
| `/api/batch/` | CRUD | 生产批次管理 |
| `/api/report/` | CRUD | 检测报告管理 |
| `/api/certificate/` | CRUD | 合格证签发 |
| `/api/certificate/{id}/qrcode/` | GET | 生成追溯二维码 |
| `/api/evidence/` | GET | 存证记录查询 |
| `/api/evidence/verify/{cert_no}/` | GET | 哈希核验 |
| `/api/public/verify/{cert_no}/` | GET | 公众扫码验真（免登录） |
| `/api/supply/` | CRUD | 产销供需信息 |

## 可信存证机制

1. 合格证签发时，后端对关键字段（证书编号、主体、批次、产品、检测结论、签发时间等）
   做规范化拼接后计算 **SHA-256 摘要**，写入存证记录表。
2. 验真时重新读取合格证当前字段，按同样规则重算哈希，与存证表中的摘要比对。
3. 任何字段被篡改，重算结果都会与存证摘要不一致，从而被识别为"不可信"。

> 哈希由后端生成，前端无法伪造。该机制可平滑迁移至联盟链（FISCO BCOS 等）。

## 目录结构

```
源代码/
├── backend/                    # Django 后端
│   ├── config/                 # 项目配置
│   ├── apps/
│   │   ├── accounts/           # 用户与角色
│   │   ├── traceability/       # 主体/批次/检测/合格证/存证/产销
│   │   └── ...
│   └── manage.py
└── frontend/                   # Vue3 前端
```

## 生产部署

```
前端 Nginx 静态托管（dist/） + /api 反向代理 → Gunicorn
```

```bash
gunicorn config.wsgi:application -b 0.0.0.0:8000 -w 4
```