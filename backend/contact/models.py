from django.db import models


class Contact(models.Model):
  """user contact details on dB"""
  id = models.BigAutoField(auto_created=True, primary_key=True)
  name: str = models.CharField(max_length=100)
  phoneNumber = models.CharField(max_length=15)
  email = models.EmailField(max_length=50)
  message = models.TextField(max_length=255)
  timestamp = models.DateTimeField(auto_now=True, db_index=True)

  class Meta:
    ordering = ["-timestamp"]
    indexes = [
      models.Index(fields=["email", "timestamp"]),
    ]

  def __str__(self) -> str:
    return f"ContactMessage(name={self.name!r}, email={self.email!r})"


class Subscribe(models.Model):
  """user subscribe details on dB"""
  id = models.BigAutoField(auto_created=True, primary_key=True)
  email = models.EmailField(max_length=50)
  createdAt = models.DateTimeField(auto_now_add=True)

  class Meta:
    ordering = ["-createdAt"]

  def __str__(self) -> str:
    return f"ContactMessage(name={self.name!r}, email={self.email!r})"

