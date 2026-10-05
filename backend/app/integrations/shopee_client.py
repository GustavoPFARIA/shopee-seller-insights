"""Minimal, typed client for Shopee Open Platform API v2.

Signing (per Shopee docs): sign = HMAC-SHA256(partner_key, base_string) as hex, where
  public APIs: base_string = partner_id + api_path + timestamp
  shop APIs:   base_string = partner_id + api_path + timestamp + access_token + shop_id
Every response is JSON with "error" / "message"; an empty "error" means success.
"""

import hashlib
import hmac
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

ORDER_LIST_MAX_RANGE_SECONDS = 15 * 24 * 3600  # API limit for get_order_list
ORDER_DETAIL_BATCH = 50
ITEM_BATCH = 50
PAGE_SIZE = 100
MAX_ATTEMPTS = 3
TOKEN_ERRORS = {"error_auth", "invalid_access_token", "invalid_acceess_token"}


class ShopeeApiError(Exception):
    def __init__(self, error: str, message: str = "", status_code: int | None = None) -> None:
        super().__init__(f"{error}: {message}" if message else error)
        self.error = error
        self.message = message
        self.status_code = status_code


class ShopeeAuthError(ShopeeApiError):
    """The shop's access token is invalid/expired (caller should refresh)."""


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expire_in: int  # seconds until the access token expires


def sign(partner_key: str, *parts: str | int) -> str:
    base = "".join(str(p) for p in parts)
    return hmac.new(partner_key.encode(), base.encode(), hashlib.sha256).hexdigest()


class ShopeeClient:
    def __init__(
        self,
        *,
        partner_id: int,
        partner_key: str,
        host: str,
        http: httpx.Client | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.partner_id = partner_id
        self._key = partner_key
        self.host = host.rstrip("/")
        self._http = http or httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0))
        self._clock = clock
        self._sleep = sleep

    # ---- auth ---------------------------------------------------------------

    def authorization_url(self, redirect_url: str) -> str:
        path = "/api/v2/shop/auth_partner"
        ts = int(self._clock())
        query = urlencode(
            {
                "partner_id": self.partner_id,
                "timestamp": ts,
                "sign": sign(self._key, self.partner_id, path, ts),
                "redirect": redirect_url,
            }
        )
        return f"{self.host}{path}?{query}"

    def get_token(self, code: str, shop_id: int) -> TokenPair:
        body = {"code": code, "shop_id": shop_id, "partner_id": self.partner_id}
        return self._token_pair(self._public_post("/api/v2/auth/token/get", body))

    def refresh_token(self, refresh_token: str, shop_id: int) -> TokenPair:
        body = {"refresh_token": refresh_token, "shop_id": shop_id, "partner_id": self.partner_id}
        return self._token_pair(self._public_post("/api/v2/auth/access_token/get", body))

    @staticmethod
    def _token_pair(data: dict[str, Any]) -> TokenPair:
        try:
            return TokenPair(
                access_token=str(data["access_token"]),
                refresh_token=str(data["refresh_token"]),
                expire_in=int(data["expire_in"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ShopeeApiError("bad_response", "token response is incomplete") from exc

    # ---- orders -------------------------------------------------------------

    def iter_order_sns(
        self, access_token: str, shop_id: int, time_from: int, time_to: int
    ) -> Iterator[str]:
        """Order numbers updated in [time_from, time_to], split into 15-day windows."""
        start = time_from
        while start < time_to:
            end = min(start + ORDER_LIST_MAX_RANGE_SECONDS, time_to)
            cursor = ""
            while True:
                data = self._shop_get(
                    "/api/v2/order/get_order_list",
                    access_token,
                    shop_id,
                    {
                        "time_range_field": "update_time",
                        "time_from": start,
                        "time_to": end,
                        "page_size": PAGE_SIZE,
                        "cursor": cursor,
                    },
                )
                response = data.get("response", {})
                for order in response.get("order_list", []):
                    yield str(order["order_sn"])
                if not response.get("more"):
                    break
                cursor = str(response.get("next_cursor", ""))
            start = end

    def get_order_details(
        self, access_token: str, shop_id: int, order_sns: list[str]
    ) -> list[dict[str, Any]]:
        # Data minimization: recipient_address is deliberately NOT requested.
        out: list[dict[str, Any]] = []
        for i in range(0, len(order_sns), ORDER_DETAIL_BATCH):
            batch = order_sns[i : i + ORDER_DETAIL_BATCH]
            data = self._shop_get(
                "/api/v2/order/get_order_detail",
                access_token,
                shop_id,
                {
                    "order_sn_list": ",".join(batch),
                    "response_optional_fields": "buyer_username,item_list",
                },
            )
            out.extend(data.get("response", {}).get("order_list", []))
        return out

    def get_escrow_detail(self, access_token: str, shop_id: int, order_sn: str) -> dict[str, Any]:
        data = self._shop_get(
            "/api/v2/payment/get_escrow_detail", access_token, shop_id, {"order_sn": order_sn}
        )
        income = data.get("response", {}).get("order_income", {})
        return dict(income)

    # ---- products / stock ---------------------------------------------------

    def iter_item_ids(self, access_token: str, shop_id: int) -> Iterator[int]:
        offset = 0
        while True:
            data = self._shop_get(
                "/api/v2/product/get_item_list",
                access_token,
                shop_id,
                {"offset": offset, "page_size": PAGE_SIZE, "item_status": "NORMAL"},
            )
            response = data.get("response", {})
            for item in response.get("item", []):
                yield int(item["item_id"])
            if not response.get("has_next_page"):
                return
            offset = int(response.get("next_offset", offset + PAGE_SIZE))

    def get_item_base_info(
        self, access_token: str, shop_id: int, item_ids: list[int]
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for i in range(0, len(item_ids), ITEM_BATCH):
            batch = item_ids[i : i + ITEM_BATCH]
            data = self._shop_get(
                "/api/v2/product/get_item_base_info",
                access_token,
                shop_id,
                {"item_id_list": ",".join(str(x) for x in batch)},
            )
            out.extend(data.get("response", {}).get("item_list", []))
        return out

    def get_model_list(self, access_token: str, shop_id: int, item_id: int) -> list[dict[str, Any]]:
        data = self._shop_get(
            "/api/v2/product/get_model_list", access_token, shop_id, {"item_id": item_id}
        )
        return list(data.get("response", {}).get("model", []))

    # ---- transport ----------------------------------------------------------

    def _public_post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        def params() -> dict[str, Any]:
            ts = int(self._clock())
            return {
                "partner_id": self.partner_id,
                "timestamp": ts,
                "sign": sign(self._key, self.partner_id, path, ts),
            }

        return self._request("POST", path, params, json=body)

    def _shop_get(
        self, path: str, access_token: str, shop_id: int, query: dict[str, Any]
    ) -> dict[str, Any]:
        def params() -> dict[str, Any]:
            ts = int(self._clock())
            return {
                "partner_id": self.partner_id,
                "timestamp": ts,
                "access_token": access_token,
                "shop_id": shop_id,
                "sign": sign(self._key, self.partner_id, path, ts, access_token, shop_id),
                **query,
            }

        return self._request("GET", path, params)

    def _request(
        self,
        method: str,
        path: str,
        params: Callable[[], dict[str, Any]],
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send with a fresh signature per attempt; retry 429/5xx/network errors."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = self._http.request(method, self.host + path, params=params(), json=json)
            except httpx.TransportError as exc:
                if attempt == MAX_ATTEMPTS:
                    raise ShopeeApiError("network_error", type(exc).__name__) from exc
                self._sleep(2**attempt)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == MAX_ATTEMPTS:
                    raise ShopeeApiError("http_error", "", resp.status_code)
                self._sleep(2**attempt)
                continue
            try:
                data: dict[str, Any] = resp.json()
            except ValueError as exc:
                raise ShopeeApiError("bad_response", "not JSON", resp.status_code) from exc
            error = str(data.get("error") or "")
            if error:
                message = str(data.get("message") or "")
                if error in TOKEN_ERRORS:
                    raise ShopeeAuthError(error, message, resp.status_code)
                raise ShopeeApiError(error, message, resp.status_code)
            return data
        raise ShopeeApiError("unreachable")  # pragma: no cover
