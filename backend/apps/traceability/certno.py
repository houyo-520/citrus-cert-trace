"""
合格证编号生成器

编号规则（对标农业农村部实际使用的 19 位格式）：
    区域码(6) + 日期(8) + 流水(5)
    └─ 510106 ┘ └ 20260923 ┘ └ 00001 ┘
    四川省成都市金牛区 / 2026-09-23 / 当天第 1 张

为什么要这么做：
  1. 编号可读 —— 从编号本身就能看出「哪里发的、哪天发的、当天第几张」，
     基层监管人员核对纸质证时无需查库。
  2. 防占号 —— 编号只在「签发」环节分配，草稿/待审核阶段 cert_no 为空。
     否则大量草稿会把流水号吃掉，正式签发时号码跳跃、且废号无法回收。
  3. 防撞号 —— 应用层「查最大流水 + 1」在并发下有竞态（两个请求可能查到同一个最大值）。
     这里靠「数据库唯一索引 + 冲突重试」兜底：即使算重了，INSERT 也会被唯一约束拦住，
     捕获 IntegrityError 后重新取号。这是并发安全的关键。
"""
import re
from datetime import date

from django.db import IntegrityError, transaction

# 编号各段长度
REGION_LEN = 6
DATE_LEN = 8
SEQ_LEN = 5
TOTAL_LEN = REGION_LEN + DATE_LEN + SEQ_LEN   # 19

# 流水号上限（5 位）
MAX_SEQ = 10 ** SEQ_LEN - 1

# 取号最大重试次数（并发冲突时）
MAX_RETRY = 20

# 编号格式：19 位纯数字
CERT_NO_PATTERN = re.compile(r"^\d{19}$")

# 区域码兜底值（主体未填区域码时使用，保证编号长度一致）
DEFAULT_REGION_CODE = "000000"


def normalize_region_code(raw: str) -> str:
    """
    把主体的区域码规整为 6 位数字。

    容错处理：
      - 空值 -> 兜底 000000
      - 非数字（如手填「四川省眉山市」）-> 兜底 000000
      - 长度不足 6 位 -> 左补 0
      - 超过 6 位 -> 取前 6 位
    """
    code = (raw or "").strip()
    if not code.isdigit():
        return DEFAULT_REGION_CODE
    if len(code) < REGION_LEN:
        return code.zfill(REGION_LEN)
    return code[:REGION_LEN]


def build_prefix(region_code: str, on_date: date = None) -> str:
    """拼出编号前缀 = 区域码 + 日期。"""
    d = on_date or date.today()
    return f"{normalize_region_code(region_code)}{d.strftime('%Y%m%d')}"


def _next_seq(prefix: str) -> int:
    """
    查询该前缀下已用的最大流水号，返回下一个可用值。

    用字符串比较取最大值：因为所有编号长度一致（19 位），
    字典序等价于数值序，比先取全量再算 max 更高效（只取一条）。
    """
    from .models import Certificate

    last = (Certificate.objects
            .filter(cert_no__startswith=prefix)
            .order_by("-cert_no")
            .values_list("cert_no", flat=True)
            .first())
    if not last or len(last) < TOTAL_LEN:
        return 1
    try:
        return int(last[-SEQ_LEN:]) + 1
    except ValueError:
        return 1


def peek_next_cert_no(producer, on_date: date = None) -> str:
    """
    预看下一个编号（不占用）。

    仅用于前端展示「即将分配 5101062026092300001」，
    正式分配必须走 allocate_cert_no（带唯一约束保护）。
    """
    prefix = build_prefix(getattr(producer, "region_code", ""), on_date)
    seq = _next_seq(prefix)
    if seq > MAX_SEQ:
        raise ValueError(f"当日流水号已用尽（上限 {MAX_SEQ}）")
    return f"{prefix}{seq:0{SEQ_LEN}d}"


def allocate_cert_no(producer, on_date: date = None) -> str:
    """
    正式分配编号（并发安全）。

    与 peek_next_cert_no 的区别：本函数假设调用方处于「签发」事务中，
    并依赖 Certificate.cert_no 的数据库唯一约束做最终裁决 ——
    如果算出的号码已被占用，捕获 IntegrityError 后重试。

    注意：这里不直接 INSERT，而是把号码返回给调用方去保存。
    所以真正的防冲突发生在调用方 save() 时；本函数负责「尽量取到不冲突的号」。
    为了减少重试，先用 _next_seq 取理论值，冲突由调用方兜底重试。
    """
    return peek_next_cert_no(producer, on_date)


def save_with_cert_no(cert, producer, on_date: date = None) -> str:
    """
    给草稿合格证分配编号并保存（自带冲突重试）。

    返回最终使用的编号。这是推荐给 ViewSet 调用的入口：
    把「取号 + 保存」放在一个循环里，冲突就重取，直到成功或超过重试上限。

    为什么不能只靠 _next_seq：并发下两个请求可能算出同一个流水号，
    此时唯一索引会拒绝第二个。捕获后重新取号即可，无需加锁 ——
    这比 select_for_update 更简单，也不依赖数据库是否支持行锁（SQLite 就不支持）。
    """
    last_error = None
    for _ in range(MAX_RETRY):
        try:
            cert_no = peek_next_cert_no(producer, on_date)
        except ValueError:
            raise
        cert.cert_no = cert_no
        try:
            with transaction.atomic():
                cert.save()
            return cert_no
        except IntegrityError as exc:
            # 号码被并发请求抢走，重新取号再试
            last_error = exc
            continue
    raise RuntimeError(f"合格证编号分配失败（重试 {MAX_RETRY} 次仍有冲突）") from last_error


def is_valid_cert_no(cert_no: str) -> bool:
    """校验编号是否符合 19 位格式（用于测试与数据体检）。"""
    return bool(cert_no) and bool(CERT_NO_PATTERN.match(cert_no.strip()))


def parse_cert_no(cert_no: str) -> dict:
    """
    解析编号各段（便于页面上展示「区域 / 日期 / 序号」）。

    返回 {region_code, date, seq, valid}；格式不符时 valid=False。
    """
    code = (cert_no or "").strip()
    if not is_valid_cert_no(code):
        return {"region_code": "", "date": "", "seq": "", "valid": False}
    region = code[:REGION_LEN]
    date_str = code[REGION_LEN:REGION_LEN + DATE_LEN]
    seq = code[REGION_LEN + DATE_LEN:]
    return {
        "region_code": region,
        "date": f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}",
        "seq": seq,
        "valid": True,
    }
