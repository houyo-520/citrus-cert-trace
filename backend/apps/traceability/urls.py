"""
业务路由
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (ProducerViewSet, ProductBatchViewSet, TestReportViewSet,
                    CertificateViewSet, EvidenceViewSet, SupplyDemandViewSet,
                    PublicVerifyView, DashboardView)

router = DefaultRouter()
router.register(r"producer", ProducerViewSet, basename="producer")
router.register(r"batch", ProductBatchViewSet, basename="batch")
router.register(r"report", TestReportViewSet, basename="report")
router.register(r"certificate", CertificateViewSet, basename="certificate")
router.register(r"evidence", EvidenceViewSet, basename="evidence")
router.register(r"supply", SupplyDemandViewSet, basename="supply")

urlpatterns = [
    path("", include(router.urls)),
    path("public/verify/<str:cert_no>/", PublicVerifyView.as_view(),
         name="public-verify"),
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
]