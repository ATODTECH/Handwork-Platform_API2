from django.utils.deprecation import MiddlewareMixin
from django.http import JsonResponse
from rest_framework.views import exception_handler
from rest_framework.renderers import JSONRenderer
from django.db import IntegrityError
import json


class CustomResponseMiddleware(MiddlewareMixin):
    def process_response(self, request, response):
        # Skip Swagger/Redoc
        if request.path.startswith('/swagger') or request.path.startswith('/redoc'):
            return response

        # Skip HTML responses
        content_type = response.headers.get('Content-Type', '')
        if 'text/html' in content_type:
            return response

        # Skip responses that don't have .data (e.g. raw HttpResponse)
        if not hasattr(response, 'data'):
            return response

        try:
            if 200 <= response.status_code < 300:
                status = True
                message = "Request Successful"
            else:
                status = False
                message = response.reason_phrase or "An error occurred"

            # ✅ Use DRF's JSONRenderer to handle UUID, Decimal, datetime safely
            data = response.data if response.data is not None else {}
            rendered = JSONRenderer().render(data)
            safe_data = json.loads(rendered)

            custom_response = {
                'status': status,
                'message': message,
                'data': safe_data
            }

            return JsonResponse(custom_response, status=response.status_code)

        except Exception as e:
            # If middleware itself crashes, return the original response untouched
            return response

    def process_exception(self, request, exception):
        response = exception_handler(exception, None)

        if response is not None:
            return self.process_response(request, response)

        if isinstance(exception, IntegrityError):
            return JsonResponse({
                'status': False,
                'message': str(exception),
                'data': {}
            }, status=400)

        return JsonResponse({
            'status': False,
            'message': str(exception),
            'data': {}
        }, status=500)

