# -*- coding: utf-8 -*-
"""
检测报告附件上传 —— 端到端验收（打在真实服务器上）

为什么不用 Django 测试客户端就够了：
  单测跑在内存库里、文件落在临时 MEDIA_ROOT，永远测不到
  「nginx 有没有正确托管 /media/」这一层 —— 而这层恰恰是
  「上传成功但打不开」最常见的坑（正则 location 抢了前缀匹配）。

验收链路（每一步都必须有断言，不能只看"没报错"）：
  1. 登录 producer，拿到 token
  2. 新建一条测试报告（自建、不消耗现有演示样本）
  3. multipart 上传附件 → 断言 201/200 且返回 file_url
  4. 用返回的 file_url 真去 GET 一次 → 断言 HTTP 200 且内容字节一致
     ↑ 这是关键一步：验证 nginx /media/ 规则真的通了
  5. 反例：传 .exe → 断言 400（后端可信边界）
  6. 反例：buyer 上传 → 断言 403
  7. 清理测试报告
"""
import io
import json
import sys
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1"
API = BASE + "/api"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  | {detail}" if detail else ""))


def req(method, path, *, token=None, json_body=None, form=None):
    """发请求。form 用 multipart 手工拼装（不引第三方依赖）。"""
    url = path if path.startswith("http") else API + path
    headers = {}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None

    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    elif form is not None:
        boundary = "----boundary" + uuid.uuid4().hex
        buf = io.BytesIO()
        for k, v in form.items():
            if isinstance(v, tuple):  # (filename, content)
                fname, content = v
                buf.write(f"--{boundary}\r\n".encode())
                buf.write(
                    f'Content-Disposition: form-data; name="{k}"; '
                    f'filename="{fname}"\r\n'.encode())
                buf.write(b"Content-Type: application/octet-stream\r\n\r\n")
                buf.write(content)
                buf.write(b"\r\n")
            else:
                buf.write(f"--{boundary}\r\n".encode())
                buf.write(
                    f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode())
                buf.write(str(v).encode())
                buf.write(b"\r\n")
        buf.write(f"--{boundary}--\r\n".encode())
        data = buf.getvalue()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"

    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def jload(b):
    try:
        return json.loads(b.decode("utf-8"))
    except Exception:
        return {}


def login(username, password="admin123"):
    st, b = req("POST", "/auth/login/", json_body={"username": username,
                                                  "password": password})
    if st != 200:
        print(f"  登录失败 {username}: HTTP {st} {b[:200]}")
        sys.exit(1)
    d = jload(b)
    # 兼容两种返回体形状
    return (d.get("data") or d).get("access") or (d.get("data") or d).get("token")


print("=" * 62)
print("检测报告附件上传 · 端到端验收")
print("=" * 62)

ptoken = login("producer")
btoken = login("buyer")
check("producer / buyer 登录取 token", bool(ptoken) and bool(btoken))

# ---- 取一个可用的批次 id ----
st, b = req("GET", "/batch/?page_size=100", token=ptoken)
d = jload(b)
batches = d.get("results") or d.get("data") or []
if isinstance(batches, dict):
    batches = batches.get("results") or []
check("能取到批次列表", len(batches) > 0, f"{len(batches)} 条")
if not batches:
    print("跳过后续：无可用批次")
    sys.exit(1)
batch_id = batches[0]["id"]

# ---- 2) 新建测试报告（自建，不碰现有样本）----
rno = "RPT-UPLOAD-" + uuid.uuid4().hex[:8].upper()
st, b = req("POST", "/report/", token=ptoken, json_body={
    "report_no": rno, "batch": batch_id, "test_org": "验收测试机构",
    "conclusion": "qualified",
})
d = jload(b)
check("新建测试报告", st in (200, 201), f"HTTP {st} {str(d)[:120]}")
if st not in (200, 201):
    sys.exit(1)
rid = d.get("id") or (d.get("data") or {}).get("id")

# ---- 3) multipart 上传 PDF ----
pdf_bytes = b"%PDF-1.4\n% verify upload\n1 0 obj\n<<>>\nendobj\ntrailer<<>>\n%%EOF\n"
st, b = req("PATCH", f"/report/{rid}/", token=ptoken,
            form={"file": ("验收报告.pdf", pdf_bytes)})
d = jload(b)
check("multipart 上传附件返回成功", st == 200, f"HTTP {st} {str(d)[:160]}")
file_url = d.get("file_url") or ""
check("返回 file_url 且以 /media/reports/ 开头",
      file_url.startswith("/media/reports/"), file_url)
check("file_name 回显原始文件名（不含存储层随机后缀）",
      d.get("file_name") == "验收报告.pdf", repr(d.get("file_name")))

# ---- 4) ★ 关键：真去 GET 一次附件（验证 nginx /media/ 规则）----
if file_url:
    st, got = req("GET", BASE + file_url)
    check("附件可通过 URL 下载（nginx /media/ 已生效）", st == 200, f"HTTP {st}")
    check("下载内容与上传字节完全一致", got == pdf_bytes,
          f"上传 {len(pdf_bytes)}B / 下载 {len(got)}B")

# ---- 5) 反例：可执行文件必须被后端拒绝 ----
st, b = req("PATCH", f"/report/{rid}/", token=ptoken,
            form={"file": ("evil.exe", b"MZ\x90\x00")})
check("拒绝 .exe 附件（后端可信边界）", st == 400, f"HTTP {st}")

# ---- 6) 反例：收购商无权上传 ----
st, b = req("PATCH", f"/report/{rid}/", token=btoken,
            form={"file": ("x.pdf", pdf_bytes)})
check("收购商上传被拒（403/404）", st in (403, 404), f"HTTP {st}")

# ---- 7) 清理 ----
st, b = req("DELETE", f"/report/{rid}/", token=ptoken)
check("清理测试报告", st in (200, 204), f"HTTP {st}")

print("-" * 62)
print(f"通过 {len(PASS)} / 共 {len(PASS) + len(FAIL)}")
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("全部通过")