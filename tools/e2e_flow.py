"""答辩演示主流程端到端验证：建档 → 提交送审 → 签发（分配19位编号+上链）→ 验真 → 二维码

★ 设计原则：脚本自己新建一条草稿来跑流程，**不消耗现有演示数据**。
   否则跑一次就把「草稿」这张演示样本签发掉，答辩现场再打开就没有草稿可演示了。
   跑完会自动把新建的那张证标记为演示残留（可在 admin 删除）。
"""
import json, urllib.request, urllib.error, time

B = "http://<your-server-ip>:8090/api"


def call(path, method="GET", data=None, token=None, retries=2):
    for attempt in range(retries + 1):
        req = urllib.request.Request(B + path, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", "Bearer " + token)
        body = json.dumps(data).encode() if data is not None else None
        try:
            with urllib.request.urlopen(req, body, timeout=30) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"_raw": raw[:200]}
        except Exception as e:
            if attempt == retries:
                return None, {"_err": f"{type(e).__name__}: {e}"}
            time.sleep(1.5)


def login(u):
    s, o = call("/auth/login/", "POST", {"username": u, "password": "admin123"})
    if s == 200 and isinstance(o, dict) and isinstance(o.get("data"), dict):
        return o["data"].get("access")
    print(f"  !! {u} 登录失败: HTTP {s} {o}")
    return None


T = {}
for u in ["admin", "regulator", "producer", "producer2", "buyer"]:
    T[u] = login(u)
print("登录:", {k: ("OK" if v else "FAIL") for k, v in T.items()})

# ---- 步骤0：新建一条草稿（不消耗演示数据）----
print("\n=== 步骤0：主体新建草稿合格证（建档，不占号）===")
# 取 producer2 的批次与报告
s, o = call("/batch/", token=T["producer2"])
b = (o or {}).get("results", [])
if not b:
    raise SystemExit("producer2 名下无批次")
batch = b[0]
print(f"  使用批次: {batch['batch_no']}")

s, o = call("/report/", token=T["producer2"])
rpts = [r for r in (o or {}).get("results", []) if r.get("batch") == batch["id"]]
if not rpts:
    print("  ✗ 该批次无检测报告，无法提交送审")
    raise SystemExit(1)
report = rpts[0]
print(f"  使用报告: {report['report_no']}")

s, o = call("/certificate/", "POST", {
    "batch": batch["id"], "report": report["id"],
    "producer_name": batch.get("producer_name") or "甜蜜蜜家庭农场",
    "product_name": "柑橘", "quantity_kg": 1500,
    "remark": "端到端演示脚本自动创建（可删除）",
}, token=T["producer2"])
print(f"  HTTP {s}")
# ★ 注意：DRF ModelViewSet 的 create 返回裸对象（无 {code,message,data} 包装），
#   而自定义 action（submit/issue）才返回包装体 —— 取值方式不同。
created = o.get("data") if isinstance(o, dict) and isinstance(o.get("data"), dict) else o
cid = (created or {}).get("id")
if not cid:
    raise SystemExit(f"建档失败: {o}")
d0 = created
print(f"  新建 #{cid}  status={d0.get('status')}  cert_no={d0.get('cert_no')!r}")
print(f"  ★ 关键：草稿 cert_no 为空 → 证明「草稿不占号」")

# ---- 步骤1：peek ----
print("\n=== 步骤1：预看下一个可用编号（不占用）===")
s, o = call("/certificate/peek-cert-no/", token=T["regulator"])
print(f"  HTTP {s} -> {json.dumps(o, ensure_ascii=False)[:240] if o else None}")

# ---- 步骤2：提交 ----
print("\n=== 步骤2：主体提交送审（draft → pending）===")
s, o = call(f"/certificate/{cid}/submit/", "POST", {}, token=T["producer2"])
print(f"  HTTP {s} -> {(o or {}).get('message') or (o or {}).get('detail')}")
if s == 200 and isinstance(o.get("data"), dict):
    d = o["data"]
    print(f"  status={d.get('status')}  cert_no={d.get('cert_no')!r} → 提交后仍不占号")

# ---- 步骤3：签发 ----
print("\n=== 步骤3：监管签发（pending → issued，分配编号 + 上链）===")
s, o = call(f"/certificate/{cid}/issue/", "POST", {}, token=T["regulator"])
print(f"  HTTP {s} -> {(o or {}).get('message') or (o or {}).get('detail')}")
cn = None
if s == 200 and isinstance(o.get("data"), dict):
    d = o["data"]
    cn = d.get("cert_no")
    print(f"  ★ 分配编号 = {cn}  (长度 {len(cn or '')})")
    print(f"  status={d.get('status')}  is_anchored={d.get('is_anchored')}")
    p = d.get("cert_no_parsed") or {}
    print(f"  编号解析 → 区域码={p.get('region_code')} 日期={p.get('date')} 流水={p.get('seq')} 合法={p.get('valid')}")

# ---- 步骤4：公开验真 ----
print("\n=== 步骤4：公开验真（免登录）===")
if cn:
    s, o = call(f"/public/verify/{cn}/")
    d = (o or {}).get("data") or {}
    print(f"  HTTP {s}  trusted={d.get('trusted')}  chain_verified={d.get('chain_verified')}")
    print(f"  status={d.get('status')}/{d.get('status_display')}  block={d.get('block_height')}")
    print(f"  链上明细: {d.get('chain_detail')}")
    print(f"  tx_hash: {str(d.get('tx_hash'))[:60]}")
else:
    print("  (跳过：签发未成功)")

# ---- 步骤5：二维码 ----
print("\n=== 步骤5：二维码解码 ===")
req = urllib.request.Request(B + f"/certificate/{cid}/qrcode/")
req.add_header("Authorization", "Bearer " + T["regulator"])
try:
    with urllib.request.urlopen(req, timeout=25) as r:
        b = r.read()
    open("_qr2.png", "wb").write(b)
    print(f"  PNG {len(b)} bytes  魔数={b[:8].hex()}")
    from PIL import Image
    import pyzbar.pyzbar as pyzbar
    res = [x.data.decode() for x in pyzbar.decode(Image.open("_qr2.png"))]
    print(f"  扫码结果: {res}")
except Exception as e:
    print(f"  二维码获取/解码失败: {type(e).__name__} {e}")

print(f"\n提示：本次自动创建的演示合格证为 #{cid}（已签发）。"
      f"如需保持「有草稿可演示」的状态，可在 Django Admin 删除它，"
      f"或重跑 init_data 会因已存在草稿而不再补建。")

