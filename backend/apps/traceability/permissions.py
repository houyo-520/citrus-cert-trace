"""
自定义权限类：按角色做接口级 + 数据级访问控制

设计要点：
1. 接口级（Permission）：用 DRF 的 BasePermission 声明「谁能调这个接口」，
   在 ViewSet 上通过 get_permissions() 按 action 细分，而不是一把 IsAuthenticated。
2. 数据级（Scope）：角色不同，可见数据范围不同 —— 种植主体只能看自己的主体/批次，
   收购商只能看已通过审核的主体与已签发的合格证，监管与管理员全量可见。
   通过统一 mixin 的 scoped_queryset() 实现，避免在每个 ViewSet 里重复写过滤。

角色矩阵（★ 答辩可直接展示）：
  | 资源           | 管理员 | 监管人员 | 种植主体        | 收购商           |
  |----------------|--------|----------|-----------------|------------------|
  | 生产主体        | 全部   | 全部     | 仅自己          | 仅已审核通过     |
  | 生产批次        | 全部   | 全部     | 仅自己的        | 仅已审核主体的   |
  | 检测报告        | 全部   | 全部     | 仅自己的        | 只读、仅合格     |
  | 合格证          | 全部   | 全部     | 自己的可增改    | 只读、仅已签发   |
  | 存证记录        | 全部   | 全部     | 仅自己的        | 只读、仅自己的   |
  | 产销供需        | 全部   | 全部     | 自己的可增改    | 自己的可增改     |
  | 操作日志        | 全部   | 全部     | 仅自己的        | 仅自己的         |
  | 主体审核        | ✓      | ✓        | ✗               | ✗                |
  | 合格证签发/作废 | ✓      | ✓        | ✗               | ✗                |
"""
from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.accounts.models import Role


# ===============================================================
# 一、接口级权限类
# ===============================================================
class IsAdmin(BasePermission):
    """仅系统管理员"""
    message = "仅系统管理员可执行该操作"

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated
                    and request.user.role == Role.ADMIN)


class IsRegulator(BasePermission):
    """监管人员或管理员（审核、签发、作废等监管动作）"""
    message = "仅监管人员或系统管理员可执行该操作"

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated
                    and request.user.role in (Role.ADMIN, Role.REGULATOR))


class IsProducer(BasePermission):
    """种植主体（可录入生产数据）"""
    message = "仅种植主体可执行该操作"

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated
                    and request.user.role == Role.PRODUCER)


class IsProducerOrRegulator(BasePermission):
    """种植主体或监管人员（生产数据的写入方）"""
    message = "仅种植主体或监管人员可执行该操作"

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated
                    and request.user.role in (Role.ADMIN, Role.REGULATOR, Role.PRODUCER))


class CanTrade(BasePermission):
    """
    产销对接权限：四个业务角色（管理员/监管/种植主体/收购商）都可参与。

    之所以不做更细的区分 —— 「产销衔接」的本质就是让供需双方都能发布信息、
    查看对手方、发起对接意向。若只放开给收购商，种植主体就无法主动找买家，
    功能上自相矛盾。真正的边界在数据级：种植主体看不到别人的供应单，
    收购商看不到别人的求购单（见 SupplyDemandViewSet.scope_queryset）。
    """
    message = "仅已登录的业务角色可执行该操作"

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated
                    and request.user.role in (Role.ADMIN, Role.REGULATOR,
                                              Role.PRODUCER, Role.BUYER))


class ReadOnlyOrRegulator(BasePermission):
    """
    读操作（GET/HEAD/OPTIONS）所有已登录用户可做；
    写操作（POST/PUT/PATCH/DELETE）仅监管人员/管理员。
    """
    message = "该资源为只读，写入需监管人员权限"

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return True
        return request.user.role in (Role.ADMIN, Role.REGULATOR)


class ReadOnlyOrProducerOrRegulator(BasePermission):
    """读所有登录用户；写入需种植主体或监管人员。"""
    message = "写入需种植主体或监管人员权限"

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return True
        return request.user.role in (Role.ADMIN, Role.REGULATOR, Role.PRODUCER)


# ===============================================================
# 二、数据级权限（查询范围隔离）
# ===============================================================
class RoleScopedQuerySetMixin:
    """
    统一的数据可见范围过滤。子类只需实现 scope_queryset(qs) 声明自己的规则，
    并保证 queryset 是「未过滤的全量」。

    约定：
      - 管理员 / 监管人员：全量
      - 种植主体：仅与自己相关的数据
      - 收购商：仅「对外可见」的数据（已审核主体、已签发合格证）
    """

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if not user or not user.is_authenticated:
            return qs.none()
        if user.role in (Role.ADMIN, Role.REGULATOR):
            return qs
        return self.scope_queryset(qs, user)

    def scope_queryset(self, qs, user):
        """默认不额外过滤，由子类覆盖。"""
        return qs


def producer_owner_qs(qs, user):
    """生产主体：种植主体只看自己创建的（owner=自己）。"""
    return qs.filter(owner=user)
