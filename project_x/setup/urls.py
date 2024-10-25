from django.contrib import admin
from django.urls import path, include
from rest_framework import routers
from main.views.user_auth import (SignUpUserView, SignInUserView, SignUpArtisanViewSet, 
                                  SkillViewSet, BankDetailViewSet, ResetPassword, RatingViewSet, get_my_ratings)
from services.views.service import ServiceViewSet, ServiceSearchViewSet, CategoryViewSet, GetServices

router = routers.DefaultRouter()

router.register("user", SignUpUserView, basename='signup-user')
router.register("artisan", SignUpArtisanViewSet, basename='artisan')
router.register("skill", SkillViewSet, basename='artisan-skill')
router.register("services", ServiceViewSet, basename="service")
router.register("category", CategoryViewSet, basename='categories')
router.register("bank_details", BankDetailViewSet, basename='artisan_bank')
router.register("service", ServiceSearchViewSet, basename='service-search')
router.register("artisan_rating", RatingViewSet, basename='rating_viewset')


urlpatterns = [
    path('admin/', admin.site.urls),
    path("api/", include(router.urls)),
    path("api/reset/password", ResetPassword.as_view(), name='reset-password'),
    path('signin/client', SignInUserView.as_view(), name='token_obtain_pair'),
    path('api/get_services', GetServices.as_view(), name='get_services'),
    path('api/my_rating/<str:pk>', get_my_ratings, name='ratings')
]