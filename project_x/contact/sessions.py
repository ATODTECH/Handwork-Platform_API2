import datetime

import jwt
from django.conf import settings
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError


def createSession(payload: dict, expireAt: int = 2419200) -> str:
  """Creates session/cookies"""
  expiration = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=expireAt)
  payload_with_exp = payload.copy()
  payload_with_exp['exp'] = expiration
  token = jwt.encode(payload_with_exp, settings.SECRET_KEY, algorithm=settings.JOSE_ALGORITHM)
  return token

def verifySession(token: str) -> dict:
  """Verify session/cookies"""
  if token == None:
    return {
      'isAuth': False,
      '_userHID': 0,
    }

  payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JOSE_ALGORITHM])
  data = payload.get('_userHID')
  return {
    'isAuth': True,
    '_userHID': data,
  }


# Initialize the hasher with secure, built-in defaults
ph = PasswordHasher()

def compute_hash(val):
  hash_password = str(ph.hash(val))
  return hash_password


def compute_value(valhashed, val):
  try:
    verify = ph.verify(valhashed, val)
    return verify
  except VerifyMismatchError:
    return False
