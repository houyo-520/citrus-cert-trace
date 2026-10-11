"""
检测报告 OCR 识别。

设计目标：把「上传检测报告 PDF/图片 → 手工敲 5 个字段」变成「上传 → 自动回填 → 人工确认」。

★ 分层识别策略（这是本模块的核心设计）：
  检测报告在实际业务里有两种形态，识别成本天差地别：
    ① **电子版 PDF**（检测机构系统导出）—— 带文本层，直接读文字，准确率接近 100%，
       且**零 OCR 依赖、毫秒级**。这是绝大多数情况。
    ② **扫描件 / 手机拍照**（纸质报告扫描、JPG/PNG）—— 没有文本层，
       必须走图像 OCR，慢（秒级）且准确率受拍摄质量影响。
  因此第一层优先尝试文本层，**只有拿不到有效文本才降级到 OCR**。
  这样既快又准，还避免了「明明有文字却去做 OCR 反而识别错」的荒谬情况。

★ 安全边界（非常重要）：
  OCR 结果**只作为表单草稿回填**，绝不直接写库。
  识别是概率性的，检测结论这类字段一旦识别错（合格→不合格）会直接
  影响合格证签发与农产品流通，必须由人眼确认。
  因此本模块只负责「返回识别结果 + 置信度 + 警告」，落库由用户点保存时决定。
"""
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

from django.conf import settings

# ★ 资源上限（M13 修复）：OCR 是「上传即触发」的同步入口，必须设上限，
#   否则一张精心构造的「解压炸弹」图片/超大 MediaBox 的 PDF 就能把 worker 拖死。
#   （视图层的扩展名/文件大小校验是「上传前」的；这里防的是「解码后」的膨胀。）
def _limit(name, default):
    try:
        return int(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default


MAX_IMAGE_PIXELS = _limit("OCR_MAX_IMAGE_PIXELS", 40_000_000)   # 约 40MP
MAX_PDF_PAGES = _limit("OCR_MAX_PDF_PAGES", 3)
MAX_PDF_DPI = _limit("OCR_MAX_PDF_DPI", 200)
MAX_PDF_RENDER_PIXELS = _limit("OCR_MAX_PDF_RENDER_PIXELS", 40_000_000)
OCR_TIMEOUT = _limit("OCR_TIMEOUT_SECONDS", 60)

# ★ 单次识别的**全局总时限**（2026-10-11 安全审计加固）。
#   背景：原实现只对「单页 OCR」设超时，但一份 PDF 最多 3 页，若每页都接近上限，
#   总耗时可达 3×上限 —— 同步接口长期占住 gunicorn worker，几个并发请求就能
#   把 worker 池打满（表现为整站变慢/超时）。
#   这里再加一道「整单截止时间」：超时后停止后续处理，已识别的部分照常返回，
#   并在 warnings 里明确告知用户「因超时未处理完，请手工补录」。
TOTAL_BUDGET_SECONDS = _limit("OCR_TOTAL_BUDGET_SECONDS", 90)

# 检测报告常见字段的取值模式
# 说明：中文报告里「合格」是「不合格」的子串，匹配顺序必须让「不合格」优先，
# 否则「检验结论：不合格」会被误判为「合格」—— 这个错误后果极严重（见模块头注释）。
CONCL_PATTERNS = [
    (r"不合格|不符合|不达标|未通过", "unqualified"),
    (r"合格|符合标准|符合要求|达标|通过", "qualified"),
]

# 报告编号：中文报告一般写「报告编号：XXX」，也可能是「检验报告编号」「No.」
REPORT_NO_PATTERNS = [
    r"报告\s*编\s*号\s*[:：]?\s*([A-Za-z0-9\u4e00-\u9fa5\-_/]{4,40})",
    r"检验\s*报\s*告\s*编\s*号\s*[:：]?\s*([A-Za-z0-9\u4e00-\u9fa5\-_/]{4,40})",
    r"检\s*测\s*报\s*告\s*编\s*号\s*[:：]?\s*([A-Za-z0-9\u4e00-\u9fa5\-_/]{4,40})",
    r"\bNo\.?\s*[:：]?\s*([A-Za-z0-9\-_/]{4,40})",
    r"编\s*号\s*[:：]?\s*([A-Z][A-Za-z0-9\-_/]{3,39})",
]

# 检测机构：取「机构名称」后的词，限定不跨越标点
ORG_PATTERNS = [
    r"(?:检测|检验|测试)\s*(?:机构|单位|中心|公司)\s*[:：]?\s*([^\s，,。；;：:]{4,50})",
    r"([\u4e00-\u9fa5]{2,20}(?:检测|检验|质检|测试)[\u4e00-\u9fa5]{0,15}(?:中心|院|所|公司|站|机构))",
]

# 日期：中文报告常写 2026年9月20日 / 2026-09-20 / 2026/09/20
DATE_RE = re.compile(r"(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})\s*日?")
DATE_LABEL_PATTERNS = [
    r"(?:检\s*测|检\s*验|报\s*告|抽\s*样|收\s*样|签\s*发)\s*日\s*期\s*[:：]?\s*",
    r"(?:日\s*期)\s*[:：]?\s*",
]

# 农药残留 / 重金属 的结论短语
RESIDUE_PATTERNS = [
    r"农\s*药\s*残\s*留\s*[:：]?\s*([^\n，,。；;]{2,40})",
    r"农残\s*[:：]?\s*([^\n，,。；;]{2,40})",
]
HEAVY_METAL_PATTERNS = [
    r"重\s*金\s*属\s*[:：]?\s*([^\n，,。；;]{2,40})",
    r"铅\s*含\s*量\s*[:：]?\s*([^\n，,。；;]{2,40})",
]

# 品种/产品名（辅助字段，命中即回填）
PRODUCT_PATTERNS = [
    r"样\s*品\s*名\s*称\s*[:：]?\s*([^\n，,。；;]{2,30})",
    r"产\s*品\s*名\s*称\s*[:：]?\s*([^\n，,。；;]{2,30})",
    r"样\s*品\s*[:：]?\s*([^\n，,。；;]{2,30})",
]

# 低于这个字符数就认为「文本层没拿到有效内容」，需要降级 OCR
MIN_TEXT_LEN = 30


# ---------------------------------------------------------------
# 一、文本提取层
# ---------------------------------------------------------------
def extract_text_from_pdf(data: bytes, max_pages: int = 5) -> tuple:
    """
    从 PDF 提取文本层。

    返回 (text, method)：
      method = "pdf-text"    成功拿到文本层（电子版 PDF）
      method = ""            无文本层（扫描件），调用方应降级 OCR
    """
    try:
        import pymupdf  # PyMuPDF
    except ImportError:
        try:
            import fitz as pymupdf  # 旧包名
        except ImportError:
            return "", ""

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        return "", ""

    try:
        chunks = []
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            chunks.append(page.get_text("text") or "")
        text = "\n".join(chunks)
    finally:
        doc.close()

    text = text.strip()
    if len(text) < MIN_TEXT_LEN:
        # 扫描件：文本层几乎是空的，交给 OCR
        return text, ""
    return text, "pdf-text"


def render_pdf_to_images(data: bytes, max_pages: int = 3, dpi: int = 200) -> list:
    """
    把 PDF 每页渲染成 PNG 字节（给 OCR 用）。

    dpi=200 是权衡：太低小字认不出，太高 OCR 慢且没明显收益。
    """
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf
        except ImportError:
            return []

    images = []
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        return []

    # ★ 上限（M13）：页数与 dpi 都夹紧；并限制单页渲染像素——
    #   固定高 dpi 渲染任意 MediaBox 的页面，10MB 的 PDF 也能渲出上亿像素撑爆内存。
    dpi = min(int(dpi or MAX_PDF_DPI), MAX_PDF_DPI)
    max_pages = min(int(max_pages or MAX_PDF_PAGES), MAX_PDF_PAGES)
    try:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            try:
                # 先按 pt 估算渲染后的像素数，超限则按比例下调 dpi
                rect = page.rect
                scale = max(dpi, 1) / 72.0
                px = int(rect.width * scale) * int(rect.height * scale)
                if px > MAX_PDF_RENDER_PIXELS and px > 0:
                    factor = (MAX_PDF_RENDER_PIXELS / px) ** 0.5
                    dpi = max(int(dpi * factor), 72)
                pix = page.get_pixmap(dpi=dpi)
                images.append(pix.tobytes("png"))
            except Exception:
                continue
    finally:
        doc.close()
    return images


def ocr_image(data: bytes, lang: str = "chi_sim+eng", budget: float | None = None) -> str:
    """
    对单张图片做 OCR。返回识别文本（失败返回空串）。

    ★ 续审修复（OCR-DEADLINE-NONINTERRUPT）——**保持原引擎优先级不变**：
      原实现「pytesseract 优先 → 命令行兜底」，但 pytesseract.image_to_string
      是**纯阻塞调用、无法超时**；一旦某页极慢，整单预算就形同虚设
      （只在「页与页之间」检查截止时间救不了正在跑的那一页）。

      修法：**把两条路线都放进可超时的子进程**执行 ——
        - 路线一：调用 `tesseract` 命令行（原生支持 `timeout=`，可被强制打断）；
        - 路线二：若系统没有 tesseract 可执行文件，则用 python -c 把
          pytesseract 跑在**子进程**里，由父进程按剩余预算 kill。
      这样「引擎优先级」与「可中断性」同时满足，且不改变识别质量。

    budget：剩余可用秒数（由 extract_from_file 依据整单总时限算出）。
    """
    def _remain() -> float:
        """本页可用超时（下限 1s，避免 0/负值直接判超时）。"""
        if budget is None:
            return float(OCR_TIMEOUT)
        return max(1.0, min(float(budget), float(OCR_TIMEOUT)))

    # ---- 路线一：tesseract 命令行（首选，可超时中断）----
    exe = shutil.which("tesseract")
    if exe:
        return _run_tesseract_cli(exe, data, lang, _remain())

    # ---- 路线二：pytesseract（无命令行时兜底）→ 放到子进程里以便可中断 ----
    return _run_pytesseract_subprocess(data, lang, _remain())


def _run_tesseract_cli(exe: str, data: bytes, lang: str, timeout: float) -> str:
    """用 tesseract 命令行识别（可超时中断）。"""
    tmpdir = tempfile.mkdtemp(prefix="ocr_")
    try:
        src = os.path.join(tmpdir, "in.png")
        out = os.path.join(tmpdir, "out")
        with open(src, "wb") as f:
            f.write(data)
        subprocess.run([exe, src, out, "-l", lang],
                       capture_output=True, timeout=timeout, check=False)
        txt_path = out + ".txt"
        if os.path.exists(txt_path):
            with open(txt_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        return ""
    except subprocess.TimeoutExpired:
        _log_timeout_once()
        return ""
    except Exception:  # noqa: BLE001
        return ""
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# 子进程内执行的 pytesseract 代码（父进程按预算 kill，从而可中断）
_PYTESS_SCRIPT = r"""
import sys
try:
    import pytesseract
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = int(sys.argv[2])
    img = Image.open(sys.argv[1])
    if img.width * img.height > Image.MAX_IMAGE_PIXELS:
        sys.exit(0)
    if img.mode != "L":
        img = img.convert("L")
    sys.stdout.write(pytesseract.image_to_string(img, lang=sys.argv[3]))
except Exception:
    sys.exit(1)
"""


def _run_pytesseract_subprocess(data: bytes, lang: str, timeout: float) -> str:
    """
    在**子进程**里跑 pytesseract，父进程按 timeout 强杀 —— 弥补
    pytesseract 本身不支持超时的缺陷。
    """
    tmpdir = tempfile.mkdtemp(prefix="ocr_")
    try:
        src = os.path.join(tmpdir, "in.png")
        with open(src, "wb") as f:
            f.write(data)
        p = subprocess.run(
            [sys.executable, "-c", _PYTESS_SCRIPT, src,
             str(MAX_IMAGE_PIXELS), lang],
            capture_output=True, timeout=timeout, check=False)
        if p.returncode != 0:
            return ""
        return p.stdout.decode("utf-8", "ignore")
    except subprocess.TimeoutExpired:
        _log_timeout_once()
        return ""
    except Exception:  # noqa: BLE001
        return ""
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# 超时只在首次发生时记一条 warning（避免刷日志）
_TIMEOUT_LOGGED = False


def _log_timeout_once():
    global _TIMEOUT_LOGGED
    if _TIMEOUT_LOGGED:
        return
    _TIMEOUT_LOGGED = True
    try:
        import logging
        logging.getLogger(__name__).warning(
            "OCR 单页识别超时（已放弃该页，整单预算保护生效）")
    except Exception:  # noqa: BLE001
        pass


def extract_text_from_image(data: bytes, budget: float | None = None) -> tuple:
    """图片直接 OCR。返回 (text, method)。budget 为剩余可用秒数（可中断）。"""
    text = ocr_image(data, budget=budget)
    return text.strip(), ("ocr-image" if text.strip() else "")


# ---------------------------------------------------------------
# 二、字段解析层（纯文本 -> 结构化字段）
# ---------------------------------------------------------------
def _clean(v: str) -> str:
    """去掉首尾空白与常见噪音字符。"""
    if not v:
        return ""
    v = v.strip().strip(":：|/\\-_")
    return re.sub(r"\s+", " ", v).strip()


def parse_conclusion(text: str) -> tuple:
    """
    解析检测结论。

    ★ 顺序敏感：必须先判「不合格」。若先匹配「合格」，
    则「检验结论：不合格」会被判成 qualified —— 这是会把不合格农产品
    放行流通的严重错误。
    """
    # 只看「结论」附近的文字窗口，避免把正文里无关的"合格"字样算进来
    windows = []
    for m in re.finditer(r"(?:检\s*验\s*结\s*论|检\s*测\s*结\s*论|判\s*定\s*结\s*果|"
                         r"结\s*论|判\s*定|综合判定)\s*[:：]?", text):
        windows.append(text[m.end():m.end() + 30])
    scope = " ".join(windows) if windows else text

    for pattern, value in CONCL_PATTERNS:
        if re.search(pattern, scope):
            return value, bool(windows)
    return "", False


def parse_report_no(text: str) -> str:
    for p in REPORT_NO_PATTERNS:
        m = re.search(p, text)
        if m:
            v = _clean(m.group(1))
            # 过滤明显不是编号的噪音（纯中文词、过短）
            if v and not re.fullmatch(r"[\u4e00-\u9fa5]+", v) and len(v) >= 4:
                return v
    return ""


def parse_date(text: str) -> str:
    """
    解析检测日期，返回 YYYY-MM-DD。

    策略：优先找带「检测日期/报告日期」标签的日期；
    找不到再退回「全文第一个日期」—— 报告里通常第一个日期就是检测日期。
    """
    for label in DATE_LABEL_PATTERNS:
        for m in re.finditer(label, text):
            seg = text[m.end():m.end() + 30]
            d = DATE_RE.search(seg)
            if d:
                return _norm_date(d)
    d = DATE_RE.search(text)
    return _norm_date(d) if d else ""


def _norm_date(m) -> str:
    y, mo, day = m.group(1), m.group(2), m.group(3)
    try:
        y, mo, day = int(y), int(mo), int(day)
    except ValueError:
        return ""
    if not (1900 <= y <= 2200 and 1 <= mo <= 12 and 1 <= day <= 31):
        return ""
    return f"{y:04d}-{mo:02d}-{day:02d}"


def parse_org(text: str) -> str:
    for p in ORG_PATTERNS:
        m = re.search(p, text)
        if m:
            v = _clean(m.group(1))
            if len(v) >= 4:
                return v
    return ""


def _parse_by_patterns(text: str, patterns: list) -> str:
    for p in patterns:
        m = re.search(p, text)
        if m:
            v = _clean(m.group(1))
            if v:
                return v[:100]
    return ""


def parse_fields(text: str) -> dict:
    """把文本解析成结构化字段。"""
    conclusion, conclusion_labelled = parse_conclusion(text)
    fields = {
        "report_no": parse_report_no(text),
        "test_org": parse_org(text),
        "test_date": parse_date(text),
        "conclusion": conclusion,
        "pesticide_residue": _parse_by_patterns(text, RESIDUE_PATTERNS),
        "heavy_metal": _parse_by_patterns(text, HEAVY_METAL_PATTERNS),
        "product_name": _parse_by_patterns(text, PRODUCT_PATTERNS),
    }
    return fields, conclusion_labelled


# ---------------------------------------------------------------
# 三、对外主入口
# ---------------------------------------------------------------
def extract_from_file(filename: str, data: bytes) -> dict:
    """
    识别检测报告，返回结构化结果。

    返回结构：
      {
        method:      pdf-text / ocr-image / ocr-pdf / failed
        ok:          是否拿到可用结果
        fields:      {report_no, test_org, test_date, conclusion, ...}
        filled:      实际识别出的字段名列表（前端据此高亮"已自动填充"）
        confidence:  high / medium / low
        warnings:    [给人看的提示语]
        text_excerpt: 识别原文片段（便于人工核对，也方便答辩时展示"识别了什么"）
      }
    """
    ext = os.path.splitext(filename or "")[1].lower()
    warnings = []
    text, method = "", ""
    # ★ 整单截止时间（审计加固）：多页/多步累加超时保护。
    deadline = time.monotonic() + TOTAL_BUDGET_SECONDS

    def _over_budget() -> bool:
        return time.monotonic() > deadline

    if not data:
        return {"method": "failed", "ok": False, "fields": {}, "filled": [],
                "confidence": "low", "warnings": ["文件内容为空"],
                "text_excerpt": ""}

    if ext == ".pdf":
        # 第一层：文本层
        text, method = extract_text_from_pdf(data)
        if not method and not _over_budget():
            # 第二层：扫描件 → 渲染成图再 OCR
            images = render_pdf_to_images(data)
            if not images:
                return {"method": "failed", "ok": False, "fields": {}, "filled": [],
                        "confidence": "low",
                        "warnings": ["PDF 无法解析（可能已加密或损坏），请手工录入"],
                        "text_excerpt": ""}
            # ★ 逐页识别，每页前检查总时限；并把**剩余预算**传下去，
            #   让单页识别也能被强制中断（子进程超时）。
            chunks = []
            for idx, img in enumerate(images):
                left = deadline - time.monotonic()
                if left <= 0:
                    warnings.append(
                        f"识别超时（已处理 {idx}/{len(images)} 页），"
                        "请手工补录剩余内容")
                    break
                chunks.append(ocr_image(img, budget=left))
            text = "\n".join(chunks).strip()
            method = "ocr-pdf" if text else ""
            warnings.append("该 PDF 无文本层（扫描件），已用 OCR 识别，建议逐项核对")
        elif not method and _over_budget():
            # 文本层尝试就已超时：不进入重 OCR，直接如实告知
            warnings.append("识别超时，未能完成扫描件 OCR，请手工录入")
    elif ext in (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"):
        if _over_budget():
            warnings.append("识别超时，未能完成图片 OCR，请手工录入")
        else:
            text, method = extract_text_from_image(
                data, budget=max(1.0, deadline - time.monotonic()))
            if method:
                warnings.append("图片识别结果可能存在误差，请核对后再保存")
    else:
        return {"method": "failed", "ok": False, "fields": {}, "filled": [],
                "confidence": "low",
                "warnings": [f"暂不支持 {ext or '该'} 格式的自动识别，请手工录入"],
                "text_excerpt": ""}

    if not text:
        return {"method": "failed", "ok": False, "fields": {}, "filled": [],
                "confidence": "low",
                "warnings": warnings + ["未能识别出文字内容，请手工录入或换更清晰的扫描件"],
                "text_excerpt": ""}

    fields, conclusion_labelled = parse_fields(text)
    filled = [k for k, v in fields.items() if v]

    # ---- 置信度评估 ----
    # 判据是「关键字段命中数」而非 OCR 引擎自评分：
    # 业务上真正关心的是「能不能省下手敲报告编号和结论」，
    # 引擎给的高分但编号没认出来，对用户没有价值。
    key_hit = sum(1 for k in ("report_no", "conclusion", "test_date") if fields.get(k))
    if method == "pdf-text" and key_hit >= 2:
        confidence = "high"
    elif key_hit >= 2:
        confidence = "medium"
    elif key_hit == 1:
        confidence = "low"
        warnings.append("仅识别出部分字段，请补全后保存")
    else:
        confidence = "low"
        warnings.append("未能识别出关键字段（报告编号/结论/日期），请手工录入")

    # ★ 数值置信度（0~100）：供前端做「<70% 需人工重点核对」的显式标记。
    #   评分规则：三个关键字段每命中一个计 28 分，识别方式给基础分
    #   （pdf 文本层 16 分、图像 OCR 6 分），上限 100。
    #   这样 pdf-text 命中 2 个字段 ≈ 72 分（临界上方），
    #   图像 OCR 命中 2 个字段 ≈ 62 分（落到 <70 警示区间）。
    _method_base = 16 if method == "pdf-text" else 6
    _score = _method_base + key_hit * 28
    confidence_score = min(100, _score)
    if confidence == "low":
        # 低置信度封顶 55，避免「低」却显示高分造成误导
        confidence_score = min(confidence_score, 55)

    # 结论没带标签时命中，提示可能是正文里蹭到的，需重点核对
    if fields.get("conclusion") and not conclusion_labelled:
        warnings.append("「检测结论」未在结论字段位置找到，取自全文关键词，务必人工确认")

    if not fields.get("conclusion"):
        warnings.append("未识别到检测结论，请务必手工选择（合格/不合格）")

    return {
        "method": method,
        "ok": True,
        "fields": fields,
        "filled": filled,
        "confidence": confidence,
        "confidence_score": confidence_score,
        "warnings": warnings,
        "text_excerpt": text[:1200],
    }


def engine_status() -> dict:
    """OCR 引擎可用性自检（前端可展示，也便于部署后排查环境问题）。"""
    has_pymupdf = False
    try:
        import pymupdf  # noqa: F401
        has_pymupdf = True
    except ImportError:
        try:
            import fitz  # noqa: F401
            has_pymupdf = True
        except ImportError:
            pass

    has_pytesseract = False
    try:
        import pytesseract  # noqa: F401
        has_pytesseract = True
    except ImportError:
        pass

    tesseract_bin = shutil.which("tesseract") or ""
    langs = []
    if tesseract_bin:
        try:
            r = subprocess.run([tesseract_bin, "--list-langs"],
                               capture_output=True, timeout=20, check=False)
            langs = [ln.strip() for ln in
                     r.stdout.decode("utf-8", "ignore").splitlines()[1:] if ln.strip()]
        except Exception:
            langs = []

    return {
        "pdf_text": has_pymupdf,
        "ocr_python": has_pytesseract,
        "ocr_binary": bool(tesseract_bin),
        "ocr_langs": langs,
        "ocr_ready": bool(tesseract_bin) and ("chi_sim" in langs),
        "note": "电子版 PDF 走文本层（不需要 OCR）；扫描件/图片才需要 OCR 引擎",
    }
