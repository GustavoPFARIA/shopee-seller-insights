"""In-memory fake of the Shopee Open Platform v2 endpoints used by the app.

It verifies signatures and timestamps exactly like the real API would, so the
client's signing is tested, and supports pagination and failure injection.
"""

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.integrations.shopee_client import ORDER_LIST_MAX_RANGE_SECONDS, sign

PARTNER_ID = 2001887
PARTNER_KEY = "fake-partner-key-for-tests-0123456789abcdef"
HOST = "https://partner.fake-shopee.test"
SHOP_ID = 99_001


@dataclass
class FakeShop:
    orders: dict[str, dict[str, Any]] = field(default_factory=dict)
    update_times: dict[str, int] = field(default_factory=dict)
    escrow: dict[str, dict[str, Any]] = field(default_factory=dict)
    items: list[dict[str, Any]] = field(default_factory=list)
    models: dict[int, list[dict[str, Any]]] = field(default_factory=dict)


class FakeShopee:
    def __init__(self, now: int) -> None:
        self.now = now
        self.shops: dict[int, FakeShop] = {SHOP_ID: FakeShop()}
        self.codes: dict[str, int] = {"good-code": SHOP_ID}
        self.access: dict[str, tuple[int, int]] = {}  # token -> (shop_id, expires_at)
        self.refresh: dict[str, int] = {}
        self.calls: Counter[str] = Counter()
        self.last_query: dict[str, dict[str, str]] = {}
        self.fail_next: list[tuple[str, int, str]] = []  # (path, http status, error)
        self._seq = 0

    # ---- helpers for tests ---------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def add_order(
        self,
        order_sn: str,
        *,
        status: str,
        create_time: int,
        items: list[dict[str, Any]],
        update_time: int | None = None,
        escrow: dict[str, Any] | None = None,
        shop_id: int = SHOP_ID,
    ) -> None:
        shop = self.shops[shop_id]
        shop.orders[order_sn] = {
            "order_sn": order_sn,
            "order_status": status,
            "create_time": create_time,
            "buyer_username": f"buyer_{order_sn.lower()}",
            "recipient_address": {"name": "SHOULD NOT BE REQUESTED", "phone": "000"},
            "item_list": items,
        }
        shop.update_times[order_sn] = update_time or create_time
        if escrow is not None:
            shop.escrow[order_sn] = escrow

    def expire_all_access_tokens(self) -> None:
        self.access = {t: (s, self.now - 1) for t, (s, _) in self.access.items()}

    def _token(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq:04d}"

    # ---- request handling ----------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls[path] += 1
        q = dict(request.url.params)
        self.last_query[path] = q
        for i, (fail_path, status, error) in enumerate(self.fail_next):
            if fail_path == path:
                del self.fail_next[i]
                return httpx.Response(status, json={"error": error, "message": "injected"})

        if int(q.get("partner_id", 0)) != PARTNER_ID:
            return self._err("error_param", "bad partner_id")
        ts = int(q.get("timestamp", 0))
        if abs(ts - self.now) > 300:
            return self._err("error_param", "timestamp expired")

        if path.startswith("/api/v2/auth/"):
            if q.get("sign") != sign(PARTNER_KEY, PARTNER_ID, path, ts):
                return self._err("error_sign", "wrong sign", 403)
            body = json.loads(request.content or b"{}")
            if path == "/api/v2/auth/token/get":
                shop_id = self.codes.pop(str(body.get("code")), None)
                if shop_id is None or shop_id != int(body.get("shop_id", 0)):
                    return self._err("error_auth", "invalid code", 403)
                return self._issue(shop_id)
            if path == "/api/v2/auth/access_token/get":
                shop_id = self.refresh.pop(str(body.get("refresh_token")), None)
                if shop_id is None:
                    return self._err("error_auth", "invalid refresh token", 403)
                return self._issue(shop_id)
            return self._err("error_not_found", "unknown path", 404)

        token = q.get("access_token", "")
        shop_id = int(q.get("shop_id", 0))
        if q.get("sign") != sign(PARTNER_KEY, PARTNER_ID, path, ts, token, shop_id):
            return self._err("error_sign", "wrong sign", 403)
        owner = self.access.get(token)
        if owner is None or owner[0] != shop_id or owner[1] <= self.now:
            return self._err("invalid_access_token", "token expired", 403)
        shop = self.shops[shop_id]
        handler = {
            "/api/v2/order/get_order_list": self._order_list,
            "/api/v2/order/get_order_detail": self._order_detail,
            "/api/v2/payment/get_escrow_detail": self._escrow,
            "/api/v2/product/get_item_list": self._item_list,
            "/api/v2/product/get_item_base_info": self._item_base_info,
            "/api/v2/product/get_model_list": self._model_list,
        }.get(path)
        if handler is None:
            return self._err("error_not_found", "unknown path", 404)
        return handler(shop, q)

    @staticmethod
    def _err(error: str, message: str, status: int = 400) -> httpx.Response:
        return httpx.Response(status, json={"error": error, "message": message})

    @staticmethod
    def _ok(response: dict[str, Any]) -> httpx.Response:
        return httpx.Response(200, json={"error": "", "message": "", "response": response})

    def _issue(self, shop_id: int) -> httpx.Response:
        access, refresh = self._token("access"), self._token("refresh")
        self.access[access] = (shop_id, self.now + 4 * 3600)
        self.refresh[refresh] = shop_id
        return httpx.Response(
            200,
            json={
                "error": "",
                "access_token": access,
                "refresh_token": refresh,
                "expire_in": 14400,
            },
        )

    def _order_list(self, shop: FakeShop, q: dict[str, str]) -> httpx.Response:
        start, end = int(q["time_from"]), int(q["time_to"])
        if end - start > ORDER_LIST_MAX_RANGE_SECONDS:
            return self._err("error_param", "time range exceeds 15 days")
        page_size = int(q.get("page_size", 20))
        matching = sorted(sn for sn, ut in shop.update_times.items() if start <= ut <= end)
        offset = int(q.get("cursor") or 0)
        page = matching[offset : offset + page_size]
        more = offset + page_size < len(matching)
        return self._ok(
            {
                "more": more,
                "next_cursor": str(offset + page_size) if more else "",
                "order_list": [{"order_sn": sn} for sn in page],
            }
        )

    def _order_detail(self, shop: FakeShop, q: dict[str, str]) -> httpx.Response:
        sns = q["order_sn_list"].split(",")
        if len(sns) > 50:
            return self._err("error_param", "too many order_sn")
        optional = set(q.get("response_optional_fields", "").split(","))
        out = []
        for sn in sns:
            order = dict(shop.orders[sn])
            if "recipient_address" not in optional:
                order.pop("recipient_address")
            out.append(order)
        return self._ok({"order_list": out})

    def _escrow(self, shop: FakeShop, q: dict[str, str]) -> httpx.Response:
        income = shop.escrow.get(q["order_sn"])
        if income is None:
            return self._err("error_not_found", "escrow not ready")
        return self._ok({"order_sn": q["order_sn"], "order_income": income})

    def _item_list(self, shop: FakeShop, q: dict[str, str]) -> httpx.Response:
        offset, size = int(q.get("offset", 0)), int(q.get("page_size", 10))
        page = shop.items[offset : offset + size]
        has_next = offset + size < len(shop.items)
        return self._ok(
            {
                "item": [{"item_id": i["item_id"]} for i in page],
                "has_next_page": has_next,
                "next_offset": offset + size,
            }
        )

    def _item_base_info(self, shop: FakeShop, q: dict[str, str]) -> httpx.Response:
        ids = {int(x) for x in q["item_id_list"].split(",")}
        return self._ok({"item_list": [i for i in shop.items if i["item_id"] in ids]})

    def _model_list(self, shop: FakeShop, q: dict[str, str]) -> httpx.Response:
        return self._ok({"model": shop.models.get(int(q["item_id"]), [])})


def stock(n: int) -> dict[str, Any]:
    return {"stock_info_v2": {"summary_info": {"total_available_stock": n}}}
