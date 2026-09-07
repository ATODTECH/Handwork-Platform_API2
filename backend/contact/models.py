from django.db import models


class Contact(models.Model):
  """user contact details on dB"""
  id = models.BigAutoField(auto_created=True, primary_key=True)
  name: str = models.CharField(max_length=100, null=False, blank=False)
  phoneNumber = models.CharField(max_length=15, null=False, blank=False)
  email = models.EmailField(max_length=50, null=False, blank=False)
  message = models.TextField(max_length=255, null=False, blank=False)
  timestamp = models.DateTimeField(auto_now=True, db_index=True)

  class Meta:
    ordering = ["-timestamp"]
    indexes = [
      models.Index(fields=["email", "timestamp"]),
    ]
  
  def __str__(self):
    return f"Email: {self.email} => Message: {self.message}"


class Subscribe(models.Model):
  """user subscribe details on dB"""
  id = models.BigAutoField(auto_created=True, primary_key=True)
  email = models.EmailField(max_length=50, null=False, blank=False)
  createdAt = models.DateTimeField(auto_now_add=True)

  class Meta:
    ordering = ["-createdAt"]

  def __str__(self):
    return f"Time: {self.createdAt} => Email: {self.email}"
