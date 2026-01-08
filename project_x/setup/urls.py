from django.contrib import admin
from django.urls import path, include
from rest_framework import routers
from main.views.user_auth import (
    SignUpUserView, SignInUserView, SignUpArtisanViewSet, 
    SkillViewSet, BankDetailViewSet, ResetPassword, RatingViewSet, 
    get_my_ratings, NotificationViewSet, LogsView
)
from services.views.service import ServiceViewSet, ServiceSearchViewSet, CategoryViewSet, GetServices
from services.views.job_process import BookArtisanViewSet, get_my_bookings, MyFilteredBookings, toggle_availability, get_today_bookings, pay_deposit, pay_balance, paystack_webhook, MaterialQuoteViewSet

router = routers.DefaultRouter()

router.register("user", SignUpUserView, basename='signup-user')
router.register("artisan", SignUpArtisanViewSet, basename='signup-artisan')
router.register("skill", SkillViewSet, basename='artisan-skill')
router.register("services", ServiceViewSet, basename="service")
router.register("category", CategoryViewSet, basename='categories')
router.register("bank_details", BankDetailViewSet, basename='artisan_bank')
router.register("service", ServiceSearchViewSet, basename='service-search')
router.register("artisan_rating", RatingViewSet, basename='rating_viewset')
router.register("book/artisan", BookArtisanViewSet, basename='book-artisan')
router.register("my_bookings/filter", MyFilteredBookings, basename='filtered-bookings')
router.register("notifications", NotificationViewSet, basename='notifications')
router.register("material_quotes", MaterialQuoteViewSet, basename='material-quote')

urlpatterns = [
    path('admin/', admin.site.urls),
    path("api/", include(router.urls)),
    path("api/reset/password", ResetPassword.as_view(), name='reset-password'),
    path('signin/client', SignInUserView.as_view(), name='token_obtain_pair'),
    path('api/get_services', GetServices.as_view(), name='get_services'),
    path('api/my_rating/<str:pk>', get_my_ratings, name='ratings'),
    path('api/my_bookings', get_my_bookings, name='user-bookings'),
    path('api/toggle_availability', toggle_availability, name="toggle-switch"),
    path('api/bookings', get_today_bookings, name="today-bookings"),
    path('api/logs/', LogsView.as_view(), name='logs'),

    path('api/book/<int:pk>/pay-deposit/', pay_deposit, name='pay-deposit'),
    path('api/book/<int:pk>/pay-balance/', pay_balance, name='pay-balance'),
    path('api/paystack/webhook/', paystack_webhook, name='paystack-webhook'),
]