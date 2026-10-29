"""
Paystack calls for paying artisans (checking a bank account, registering it as a transfer recipient,
sending transfers) and for refunding customers. Customer payments use the `paystackapi` client in
services/views/job_process.py.
"""
import logging

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger('main')

API_URL = "https://api.paystack.co"
TIMEOUT_SECONDS = 15
BANKS_CACHE_KEY = "paystack:banks:nigeria"
BANKS_CACHE_SECONDS = 60 * 60 * 24


class PaystackError(Exception):
    """Paystack refused the request (the message is safe to show) or couldn't be reached"""


def _request(method, path, **kwargs):
    try:
        resp = requests.request(
            method,
            f"{API_URL}{path}",
            headers={"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"},
            timeout=TIMEOUT_SECONDS,
            **kwargs,
        )
        body = resp.json()
    except (requests.RequestException, ValueError) as error:
        logger.error(f"Paystack {method} {path} failed: {error}")
        raise PaystackError("We couldn't reach Paystack. Please try again.") from error
    if not body.get("status"):
        logger.warning(f"Paystack {method} {path} refused: {body.get('message')}")
        raise PaystackError(body.get("message") or "Paystack refused the request.")
    return body.get("data")


def bank_code_for(bank_name):
    """The Paystack code of a Nigerian bank, by its name (older bank details only stored the name)"""
    banks = cache.get(BANKS_CACHE_KEY)
    if banks is None:
        banks = {bank["name"].strip().lower(): bank["code"] for bank in _request("GET", "/bank", params={"country": "nigeria", "perPage": 100})}
        cache.set(BANKS_CACHE_KEY, banks, BANKS_CACHE_SECONDS)
    return banks.get((bank_name or "").strip().lower())


def resolve_account(account_number, bank_code):
    """The account holder's name, as the bank has it"""
    data = _request("GET", "/bank/resolve", params={"account_number": account_number, "bank_code": bank_code})
    return data["account_name"]


def create_recipient(account_name, account_number, bank_code):
    """Registers a bank account Paystack can send transfers to; returns its recipient code"""
    data = _request("POST", "/transferrecipient", json={
        "type": "nuban",
        "name": account_name,
        "account_number": account_number,
        "bank_code": bank_code,
        "currency": "NGN",
    })
    return data["recipient_code"]


def send_transfer(amount, recipient_code, reference, reason):
    """Sends `amount` naira from the Paystack balance; returns Paystack's transfer (status, transfer_code)"""
    return _request("POST", "/transfer", json={
        "source": "balance",
        "amount": int(amount * 100),
        "recipient": recipient_code,
        "reference": reference,
        "reason": reason,
    })


def refund_charge(reference):
    """Refunds a customer's payment in full; returns Paystack's refund (status: pending or processed)"""
    return _request("POST", "/refund", json={"transaction": reference})
