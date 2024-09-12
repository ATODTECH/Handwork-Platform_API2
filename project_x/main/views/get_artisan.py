# from main.models import User, ArtisanProfile
# from main.serializers.auth_serializers import UserSerializer
# from rest_framework.views import APIView
# from rest_framework.response import Response
# from rest_framework import status
# from django.http import JsonResponse
# import math


# class FindArtisan(APIView):
#     """
#     Finding the nearest Artisan by using Haversine formula.
#     Process: Taking the current Longitude and Latitude of the current user and comparing it with the longitude and latitude of all Artisans that matches the job description
#     """
    
#     def post(self, request):
#         current_lon = request.query_params.get("current_lon")
#         current_lat = request.query_params.get("current_lat")
        
#         job_type = request.data.get("job_type")
        
#         if current_lat or current_lon is None:
#             return JsonResponse({"status": "Error", "message": "Current latitude and longitude must be provided"})
        
        
#         self.get_nearest_artisan(job_type, current_lon, current_lat)
        
        
#     def get_nearest_artisan(self, job_type, current_lon, current_lat):
#         min_distance = float('inf')
#         closest_artisan = None
        
#         try:
#             artisans = ArtisanProfile.objects.filter(type=job_type)
#         except ArtisanProfile.DoesNotExist:
#             return Response({"status": "Not Found", "message": "No Artisan with this Job Type Available"}, status=status.HTTP_404_NOT_FOUND)
#         return artisan
    
#         for artisan in artisans:
#             distance = self.haversine(current_lon, current_lat, artisan.longitude, artisan.latitude)
#             if distance < min_distance:
#                 min_distance = distance
#                 closest_artisan = artisan
            
#         if closest_artisan:
#             return UserSerializer(closest_artisan).data
#         else:
#             return JsonResponse({"status": "Error", "message": "No Users found"})
    
    
#     def haversine(self, lon1, lat1, lon2, lat2):
#         # Convert decimal degrees to radians
#         lon1, lat1, lon2, lat2 = map(math.radians, [lon1, lat1, lon2, lat2])
        
#         # Haversine formula
#         dlon = lon2 - lon1
#         dlat = lat2 - lat1
#         a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(dlon / 2) ** 2
#         c = 2 * math.asin(math.sqrt(a))

#         # Radius of Eath in kilometers. User 3956 for miles
#         r = 6371
#         return c * r