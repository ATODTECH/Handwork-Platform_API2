from django.contrib import admin
from django.urls import path, include
from rest_framework import routers


from main.views.user_auth import SignUpUserView, SignInUserView, ArtisanProfileView
from services.views.service import ServiceTypeViewSet, CategoryViewSet

router = routers.DefaultRouter()

router.register("signup/user", SignUpUserView, basename='signup-user')
router.register("artisan-profile", ArtisanProfileView, basename='artisan_profile')
router.register("services", ServiceTypeViewSet, basename="service-type")
router.register("category", CategoryViewSet, basename='categories')


urlpatterns = [
    path('admin/', admin.site.urls),
    path("api/", include(router.urls)),
    
    path('signin/client', SignInUserView.as_view(), name='token_obtain_pair'),
    # path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
]
