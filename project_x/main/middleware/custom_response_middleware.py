from django.utils.deprecation import MiddlewareMixin
from django.http import JsonResponse
from rest_framework.views import exception_handler
from django.db import IntegrityError
import traceback

class CustomResponseMiddleware(MiddlewareMixin):
    def process_response(self, request, response):
        # Skip custom processing for Swagger/OpenAPI schema views
        if request.path.startswith('/swagger') or request.path.startswith('/redoc'):
            return response

        # Skip custom processing for HTML responses (e.g., Swagger UI)
        if 'text/html' in response['Content-Type']:
            return response

        # Check if it's an error (e.g., 4xx or 5xx)
        if 200 <= response.status_code < 300:
            status = True
            message = "Request Successful"
            data = response.data if hasattr(response, 'data') else {}
        else:
            status = False
            message = response.reason_phrase or "An error occurred"
            data = response.data if hasattr(response, 'data') else {}

        # Build the custom response structure
        custom_response = {
            'status': status,
            'message': message,
            'data': data
        }

        # Return the custom formatted response as a JsonResponse
        return JsonResponse(custom_response, status=response.status_code)

    def process_exception(self, request, exception):
        # Use DRF's exception handler first
        response = exception_handler(exception, None)

        if response is not None:
            return self.process_response(request, response)

        # Handle IntegrityError and other exceptions explicitly
        if isinstance(exception, IntegrityError):
            return JsonResponse({
                'status': False,
                'message': str(exception),  # Return the detailed exception message
                'data': traceback.format_exc()  # Optional: full stack trace in response (for debugging)
            }, status=400)

        # For non-DRF handled exceptions
        return JsonResponse({
            'status': False,
            'message': str(exception),  # Return the exception message
            'data': traceback.format_exc()  # Optional: stack trace for debugging
        }, status=500)
