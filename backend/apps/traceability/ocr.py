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
import tempfile

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

    try:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            try:
                pix = page.get_pixmap(dpi=dpi)
                images.append(pix.tobytes("png"))
            except Exception:
                continue
    finally:
        doc.close()
    return images


def ocr_image(data: bytes, lang: str = "chi_sim+eng") -> str:
    """
    对单张图片做 OCR。

    优先用 pytesseract（Python 封装）；不可用时回退到直接调 tesseract 命令行 ——
    这样即使生产环境漏装了 pytesseract 也不会整个功能失效。
    """
    # 路线一：pytesseract
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        # 灰度化能显著提升中文小字的识别率（去彩色干扰）
        if img.mode != "L":
            img = img.convert("L")
        return pytesseract.image_to_string(img, lang=lang)
    except Exception:
        pass

    # 路线二：命令行 tesseract
    exe = shutil.which("tesseract")
    if not exe:
        return ""
    tmpdir = tempfile.mkdtemp(prefix="ocr_")
    try:
        src = os.path.join(tmpdir, "in.png")
        out = os.path.join(tmpdir, "out")
        with open(src, "wb") as f:
            f.write(data)
        subprocess.run([exe, src, out, "-l", lang],
                       capture_output=True, timeout=60, check=False)
        txt_path = out + ".txt"
        if os.path.exists(txt_path):
            with open(txt_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        return ""
    except Exception:
        return ""
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def extract_text_from_image(data: bytes) -> tuple:
    """图片直接 OCR。返回 (text, method)。"""
    text = ocr_image(data)
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

    if not data:
        return {"method": "failed", "ok": False, "fields": {}, "filled": [],
                "confidence": "low", "warnings": ["文件内容为空"],
                "text_excerpt": ""}

    if ext == ".pdf":
        # 第一层：文本层
        text, method = extract_text_from_pdf(data)
        if not method:
            # 第二层：扫描件 → 渲染成图再 OCR
            images = render_pdf_to_images(data)
            if not images:
                return {"method": "failed", "ok": False, "fields": {}, "filled": [],
                        "confidence": "low",
                        "warnings": ["PDF 无法解析（可能已加密或损坏），请手工录入"],
                        "text_excerpt": ""}
            chunks = [ocr_image(img) for img in images]
            text = "\n".join(chunks).strip()
            method = "ocr-pdf" if text else ""
            warnings.append("该 PDF 无文本层（扫描件），已用 OCR 识别，建议逐项核对")
    elif ext in (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"):
        text, method = extract_text_from_image(data)
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
