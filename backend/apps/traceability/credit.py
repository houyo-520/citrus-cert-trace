"""
主体信用评分算法（监管闭环的新增亮点）

为什么需要这个模块：
  系统已有「五维智能匹配」（matching.py 给供需打分），但缺少「给生产主体
  打信用分」的能力。监管人员的核心诉求是「快速识别哪些主体风险最高」，
  而不是翻开每一张检查记录手动判断。本模块把一个主体的历史行为收敛成一个
  可解释的 0-100 信用分 + 等级，并在证流通时给出风险画像。

评分模型（负向扣分制，满分 100 起步）：
  ┌──────────────┬──────┬──────────────────────────────────────┐
  │ 扣分项        │ 分值 │ 依据                                 │
  ├──────────────┼──────┼──────────────────────────────────────┤
  │ 检查不合格    │ -10  │ InspectionRecord.check_result=不合格 │
  │ 整改逾期      │ -15  │ 已逾期且未完成整改（is_overdue）      │
  │ 复查仍不合格  │ -15  │ rectify_status=复查仍不合格          │
  │ 合格证作废    │ -20  │ Certificate.status=revoked           │
  │ 检测报告不合格│ -8   │ TestReport.conclusion=不合格         │
  └──────────────┴──────┴──────────────────────────────────────┘

等级映射（可解释，答辩时能逐条讲「为什么这个主体只有 55 分」）：
  90~100  优秀
  75~89   良好
  60~74   一般
  <60     警示

设计取舍：
  - 用负向扣分而非正向加分：信用分的天花板应是「无污点=满分」，
    正向加分容易让「新主体」吃亏（没历史=低分，不合理）。
  - 每个扣分项都能回溯到具体对象（哪次检查/哪张证），评分明细可展示。
  - 与 matching.py 的信用因子（+10）同源：主体已审核且证已签发是「加分」，
    这里是「扣分」，两者正交，不冲突。
"""
from .models import Certificate, InspectionRecord, Producer, TestReport

# 扣分权重（集中管理，便于调参和答辩展示）
DEDUCTIONS = {
    "unqualified_inspection": 10,   # 每次检查不合格
    "overdue_rectify": 15,          # 每个逾期未整改
    "recheck_failed": 15,           # 每个复查仍不合格
    "revoked_certificate": 20,      # 每张作废证
    "unqualified_report": 8,        # 每份不合格报告
}

FULL_SCORE = 100
FLOOR_SCORE = 0    # 信用分下限，不出现负数


def grade_of(score: int) -> str:
    """把信用分映射为四级风险等级。"""
    if score >= 90:
        return "优秀"
    if score >= 75:
        return "良好"
    if score >= 60:
        return "一般"
    return "警示"


def score_producer(producer: Producer) -> dict:
    """
    计算单个主体的信用分。

    返回：
      {
        "score": 85,
        "grade": "良好",
        "deductions": [            # 可解释明细，每条都能回溯
          {"item": "整改逾期", "points": -15, "ref": "检查#12"},
          ...
        ],
        "stats": {                 # 原始统计，供看板展示
          "unqualified_inspections": 1,
          "overdue_rectifies": 1,
          "recheck_failed": 0,
          "revoked_certificates": 0,
          "unqualified_reports": 1,
          "total_inspections": 3,
          "total_certificates": 5,
        },
      }
    """
    deductions = []
    stats = {
        "unqualified_inspections": 0,
        "overdue_rectifies": 0,
        "recheck_failed": 0,
        "revoked_certificates": 0,
        "unqualified_reports": 0,
        "total_inspections": 0,
        "total_certificates": 0,
    }

    # 主体 -> 批次 -> 证（多级关联）
    certificates = Certificate.objects.filter(batch__producer=producer)
    stats["total_certificates"] = certificates.count()

    # 1) 作废证
    for cert in certificates.filter(status=Certificate.Status.REVOKED):
        deductions.append({
            "item": "合格证作废",
            "points": -DEDUCTIONS["revoked_certificate"],
            "ref": cert.cert_no or f"证#{cert.id}",
        })
        stats["revoked_certificates"] += 1

    # 2) 检测报告不合格（经批次关联）
    reports = TestReport.objects.filter(
        batch__producer=producer,
        conclusion=TestReport.Conclusion.UNQUALIFIED,
    )
    stats["unqualified_reports"] = reports.count()
    for r in reports:
        deductions.append({
            "item": "检测报告不合格",
            "points": -DEDUCTIONS["unqualified_report"],
            "ref": r.report_no or f"报告#{r.id}",
        })

    # 3) 监督检查（检查不合格 / 整改逾期 / 复查仍不合格）
    inspections = InspectionRecord.objects.filter(certificate__batch__producer=producer)
    stats["total_inspections"] = inspections.count()
    for insp in inspections:
        if insp.check_result == InspectionRecord.CheckResult.UNQUALIFIED:
            deductions.append({
                "item": "检查不合格",
                "points": -DEDUCTIONS["unqualified_inspection"],
                "ref": f"检查#{insp.id}",
            })
            stats["unqualified_inspections"] += 1
        if insp.rectify_status == InspectionRecord.RectifyStatus.RECHECK_FAILED:
            deductions.append({
                "item": "复查仍不合格",
                "points": -DEDUCTIONS["recheck_failed"],
                "ref": f"检查#{insp.id}",
            })
            stats["recheck_failed"] += 1
        elif insp.is_overdue:
            deductions.append({
                "item": "整改逾期",
                "points": -DEDUCTIONS["overdue_rectify"],
                "ref": f"检查#{insp.id}",
            })
            stats["overdue_rectifies"] += 1

    total_deduction = sum(d["points"] for d in deductions)
    score = max(FLOOR_SCORE, FULL_SCORE + total_deduction)

    return {
        "score": score,
        "grade": grade_of(score),
        "deductions": deductions,
        "stats": stats,
    }


def certificate_risk(cert: Certificate) -> dict:
    """
    证风险画像：结合「主体信用 + 证状态 + 报告结论」给一张证的流通风险。

    返回 {level: normal/low/high, tips: [...], credit_score, credit_grade}
      level 判定优先级（高到低）：
        high  —— 证已作废/暂停，或主体信用「警示」
        low   —— 主体信用「一般」，或证的报告结论为不合格
        normal —— 其余
    """
    tips = []
    level = "normal"
    credit_score, credit_grade = None, None

    # 证本身状态
    if cert.status == Certificate.Status.REVOKED:
        level = "high"
        tips.append("该合格证已作废，不可流通")
    elif cert.status == Certificate.Status.SUSPENDED:
        level = "high"
        tips.append("该合格证已暂停使用（监督检查不合格）")

    # 主体信用（若有关联主体）
    producer = cert.batch.producer if cert.batch_id else None
    if producer is not None:
        credit = score_producer(producer)
        credit_score, credit_grade = credit["score"], credit["grade"]
        if credit_grade == "警示" and level != "high":
            level = "high"
            tips.append(f"生产主体信用为「警示」（{credit_score} 分）")
        elif credit_grade in ("一般",) and level == "normal":
            level = "low"
            tips.append(f"生产主体信用「一般」（{credit_score} 分）")

    # 报告结论
    if cert.report_id and cert.report.conclusion == TestReport.Conclusion.UNQUALIFIED:
        if level == "normal":
            level = "low"
        tips.append("关联检测报告结论为不合格")

    if not tips:
        tips.append("未发现风险项")

    return {
        "level": level,
        "tips": tips,
        "credit_score": credit_score,
        "credit_grade": credit_grade,
    }