from django.urls import path, include
from django.http import JsonResponse
from django.contrib import admin
from django.urls import path, include
from rest_framework import routers
from main.views.user_auth import (
    SignUpUserView, SignInUserView, SignUpArtisanViewSet,
    SkillViewSet, BankDetailViewSet, ResetPassword, RatingViewSet, delete_account,
    get_my_ratings, NotificationViewSet,
    ForgotPasswordRequestOTP, ForgotPasswordVerifyOTP
)
from main.views.admin_artisans import AdminArtisanViewSet
from services.views.payouts import AdminWithdrawalViewSet, WithdrawalViewSet, wallet
from services.views.admin_overview import admin_overview
from services.views.admin_bookings import AdminBookingViewSet
from services.views.service import ServiceViewSet, ServiceSearchViewSet, CategoryViewSet, GetServices
from services.views.job_process import ArtisanPaymentsView, BookArtisanViewSet, get_my_bookings, MyFilteredBookings, toggle_availability, get_today_bookings, pay_booking, verify_booking_payment, confirm_booking, cancel_booking, report_problem, pay_deposit, pay_balance, paystack_webhook, MaterialQuoteViewSet, ArtisanEarningsView, ArtisanEarningsFilterView
from rest_framework_simplejwt.views import TokenRefreshView

def health_check(request):
    return JsonResponse({"status": "ok", "service": "User Artisan API", "version": "1.0"})

router = routers.DefaultRouter(trailing_slash=False)

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
router.register("admin/artisans", AdminArtisanViewSet, basename="admin-artisans")
router.register("withdrawals", WithdrawalViewSet, basename="withdrawals")
router.register("admin/withdrawals", AdminWithdrawalViewSet, basename="admin-withdrawals")
router.register("admin/bookings", AdminBookingViewSet, basename="admin-bookings")

urlpatterns = [
    path('', health_check, name='health-check'),
    path('admin/', admin.site.urls),
    path('', include('chat.urls')),
    path("api/reset/password", ResetPassword.as_view(), name='reset-password'),
    path('signin/client', SignInUserView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/get_services', GetServices.as_view(), name='get_services'),
    path('api/my_rating/<str:pk>', get_my_ratings, name='ratings'),
    path('api/my_bookings', get_my_bookings, name='user-bookings'),
    path('api/toggle_availability', toggle_availability, name="toggle-switch"),
    path('api/bookings', get_today_bookings, name="today-bookings"),
    path('api/wallet', wallet, name='wallet'),
    path('api/admin/overview', admin_overview, name='admin-overview'),
    path('api/book/<int:pk>/pay', pay_booking, name='pay-booking'),
    path('api/book/<int:pk>/verify-payment', verify_booking_payment, name='verify-booking-payment'),
    path('api/book/<int:pk>/confirm', confirm_booking, name='confirm-booking'),
    path('api/book/<int:pk>/cancel', cancel_booking, name='cancel-booking'),
    path('api/book/<int:pk>/report', report_problem, name='report-problem'),
    path('api/book/<int:pk>/pay-deposit/', pay_deposit, name='pay-deposit'),
    path('api/book/<int:pk>/pay-balance/', pay_balance, name='pay-balance'),
    path('api/paystack/webhook/', paystack_webhook, name='paystack-webhook'),
    path('api/artisan/earnings/', ArtisanEarningsView.as_view(), name='artisan-earnings'),
    path('api/artisan/earnings/filter/', ArtisanEarningsFilterView.as_view(), name='artisan-earnings-filter'),
    path('api/artisan/payments/', ArtisanPaymentsView.as_view(), name='artisan-payments'),
    path("api/forgot-password/request-otp", ForgotPasswordRequestOTP.as_view(), name='forgot-password-request-otp'),
    path("api/forgot-password/verify-otp", ForgotPasswordVerifyOTP.as_view(), name='forgot-password-verify-otp'),
    path('api/delete-account', delete_account, name='delete_account'),

    path('v2/contact/', include('contact.urls')),

    path("api/", include(router.urls)),
]