from django.contrib import admin
from django.urls import path, include
from rest_framework import routers
from main.views.user_auth import SignUpUserView, SignInUserView, SignUpArtisanViewSet, SkillViewSet, BankDetailViewSet, ResetPassword
from services.views.service import ServiceViewSet, CategoryViewSet, ServiceBookingViewSet


router = routers.DefaultRouter()

router.register("user", SignUpUserView, basename='signup-user')
router.register("artisan", SignUpArtisanViewSet, basename='artisan')
router.register("skill", SkillViewSet, basename='artisan-skill')
router.register("services", ServiceViewSet, basename="service-type")
router.register("category", CategoryViewSet, basename='categories')
router.register("bank_details", BankDetailViewSet, basename='artisan_bank')
router.register("booking", ServiceBookingViewSet, basename='service-booking')

urlpatterns = [
    path('admin/', admin.site.urls),
    path("api/", include(router.urls)),
    path("api/reset/password", ResetPassword.as_view(), name='reset-password'),
    
    path('signin/client', SignInUserView.as_view(), name='token_obtain_pair'),
]
