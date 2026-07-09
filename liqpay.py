"""Інтеграція з LiqPay Checkout API (оплата карткою).

Документація: https://www.liqpay.ua/documentation/api/

Потік:
1. generate_checkout() — будує data+signature, повертає URL для iframe/redirect.
2. Користувач оплачує на сторінці LiqPay.
3. LiqPay шле POST на server_url (вебхук) із data+signature.
4. verify_webhook() — перевіряє підпис, повертає розкодований payload.
"""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Optional

CHECKOUT_URL = "https://www.liqpay.ua/api/3/checkout"


def _str_to_sign(private_key: str, data_b64: str) -> str:
    """SHA1(private_key + data_b64 + private_key), base64."""
    sign = hashlib.sha1((private_key + data_b64 + private_key).encode("utf-8")).digest()
    return base64.b64encode(sign).decode("ascii")


def _encode_data(payload: dict, private_key: str) -> tuple[str, str]:
    data_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    data_b64 = base64.b64encode(data_json.encode("utf-8")).decode("ascii")
    signature = _str_to_sign(private_key, data_b64)
    return data_b64, signature


def generate_checkout(
    public_key: str,
    private_key: str,
    amount_uah: float,
    order_id: str,
    description: str = "Premium підписка",
    server_url: str = "",
    result_url: str = "",
) -> tuple[str, str, str]:
    """Будує LiqPay checkout. Повертає (checkout_url, data_b64, signature).

    checkout_url можна відкрити в браузері/iframe або показати через
    Telegram Web App openLink."""
    payload = {
        "public_key": public_key,
        "version": 3,
        "action": "pay",
        "amount": str(amount_uah),
        "currency": "UAH",
        "description": description,
        "order_id": order_id,
        "language": "uk",
        "sandbox": "0",  # змінити на '1' для тестового режиму
    }
    if server_url:
        payload["server_url"] = server_url
    if result_url:
        payload["result_url"] = result_url

    data_b64, signature = _encode_data(payload, private_key)
    return CHECKOUT_URL, data_b64, signature


def build_checkout_url(
    public_key: str,
    private_key: str,
    amount_uah: float,
    order_id: str,
    description: str = "Premium підписка",
    server_url: str = "",
    result_url: str = "",
) -> str:
    """Повертає повну URL для LiqPay checkout (data+signature в query)."""
    _, data_b64, signature = generate_checkout(
        public_key, private_key, amount_uah, order_id, description, server_url, result_url
    )
    return f"{CHECKOUT_URL}?data={data_b64}&signature={signature}"


def verify_webhook(private_key: str, data_b64: str, signature: str) -> Optional[dict]:
    """Перевіряє підпис вебхуку від LiqPay.
    Повертає розкодований payload (dict) або None, якщо підпис невірний."""
    if not private_key or not data_b64 or not signature:
        return None
    expected = _str_to_sign(private_key, data_b64)
    if expected != signature:
        return None
    try:
        decoded = base64.b64decode(data_b64).decode("utf-8")
        return json.loads(decoded)
    except Exception:
        return None


def is_payment_success(payload: dict) -> bool:
    """Чи вважати платіж успішним (статус success або sandbox)."""
    if not payload:
        return False
    status = str(payload.get("status", "")).lower()
    return status in ("success", "sandbox", "wait_accept")
