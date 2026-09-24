"""
业务路由
"""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (AlertView, CertificateViewSet, DashboardView,
                    EvidenceViewSet, InspectionRecordViewSet,
                    MatchIntentViewSet, OperationLogViewSet, ProducerViewSet,
                    ProductBatchViewSet, PublicVerifyView, RoleMatrixView,
                    SupplyDemandViewSet, TestReportViewSet)

router = DefaultRouter()
router.register(r"producer", ProducerViewSet, basename="producer")
router.register(r"batch", ProductBatchViewSet, basename="batch")
router.register(r"report", TestReportViewSet, basename="report")
router.register(r"certificate", CertificateViewSet, basename="certificate")
router.register(r"inspection", InspectionRecordViewSet, basename="inspection")
router.register(r"evidence", EvidenceViewSet, basename="evidence")
router.register(r"supply", SupplyDemandViewSet, basename="supply")
router.register(r"intent", MatchIntentViewSet, basename="intent")
router.register(r"log", OperationLogViewSet, basename="log")

urlpatterns = [
    path("", include(router.urls)),
    path("public/verify/<str:cert_no>/", PublicVerifyView.as_view(),
         name="public-verify"),
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
    path("alerts/", AlertView.as_view(), name="alerts"),
    path("role-matrix/", RoleMatrixView.as_view(), name="role-matrix"),
]
