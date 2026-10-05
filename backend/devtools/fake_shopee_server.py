"""A fake Shopee Open Platform you can click through, for the local demo only.

    docker compose -f docker-compose.yml -f docker-compose.shopee-demo.yml up --build

It serves the same v2 endpoints the app uses (signatures and timestamps are checked
exactly like the real API, see devtools/fake_shopee.py), a consent page for the
OAuth flow, and a small page with a "Create a new order" button that sends a signed
push notification (webhook) to the app. Never expose it publicly.
"""

import hashlib
import hmac
import html
import json
import os
import random
import threading
import time
import urllib.request
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from app.seed import CATALOG
from devtools.fake_shopee import PARTNER_KEY, SHOP_ID, FakeShopee, stock

PORT = int(os.environ.get("FAKE_SHOPEE_PORT", "9555"))
# Where the push is delivered (inside the compose network) and the URL that is signed
# (the one "registered on the Shopee console", i.e. the app's SHOPEE_PUSH_URL).
PUSH_TARGET = os.environ.get("FAKE_SHOPEE_PUSH_TARGET", "http://api:8000/api/shopee/push")
PUSH_URL = os.environ.get("SHOPEE_PUSH_URL", "http://localhost:8080/api/shopee/push")
SHOP_NAME = "Demo Gadgets Store (Shopee)"
DAY = 86_400

lock = threading.Lock()
fake = FakeShopee(now=int(time.time()))
rng = random.Random(2024)  # noqa: S311 (fake demo data)
_seq = 0


def _item(sku: str, name: str, price: float, qty: int) -> dict[str, object]:
    return {
        "item_sku": sku,
        "model_sku": "",
        "item_name": name,
        "model_discounted_price": price,
        "model_quantity_purchased": qty,
    }


def _escrow(gross: float) -> dict[str, float]:
    return {
        "commission_fee": round(gross * 0.14, 2),
        "service_fee": round(gross * 0.06, 2),
        "seller_transaction_fee": round(gross * 0.02, 2),
        "voucher_from_seller": rng.choice([0, 0, 0, 5]),
        "actual_shipping_fee": 18.0,
        "shopee_shipping_rebate": 12.0,
        "buyer_paid_shipping_fee": 6.0,
    }


def new_order(created: int, status: str) -> str:
    """Add one order made of demo catalogue products; returns its order_sn."""
    global _seq
    _seq += 1
    order_sn = f"SHP{datetime.fromtimestamp(created, UTC):%y%m%d}{_seq:05d}"
    picks = [
        (p, rng.randint(1, 2)) for p in rng.sample(CATALOG[:12], k=1 if rng.random() < 0.7 else 2)
    ]
    items = [_item(p.sku, p.name, float(p.price), qty) for p, qty in picks]
    gross = sum(float(p.price) * qty for p, qty in picks)
    escrow = _escrow(gross) if status in ("COMPLETED", "SHIPPED", "READY_TO_SHIP") else None
    fake.add_order(order_sn, status=status, create_time=created, items=items, escrow=escrow)
    return order_sn


def seed() -> None:
    now = fake.now
    for _ in range(120):
        status = rng.choices(
            ["COMPLETED", "SHIPPED", "READY_TO_SHIP", "CANCELLED", "UNPAID"], [70, 10, 8, 8, 4]
        )[0]
        new_order(now - rng.randint(0, 45 * DAY), status)
    fake.shops[SHOP_ID].items = [
        {
            "item_id": 1000 + n,
            "item_sku": p.sku,
            "item_name": p.name,
            "has_model": False,
            **stock(rng.randint(0, 120)),
        }
        for n, p in enumerate(CATALOG)
    ]


def send_push(order_sn: str) -> str:
    body = json.dumps(
        {
            "code": 3,
            "shop_id": SHOP_ID,
            "timestamp": int(time.time()),
            "data": {"ordersn": order_sn, "status": "READY_TO_SHIP"},
        }
    ).encode()
    signature = hmac.new(
        PARTNER_KEY.encode(), PUSH_URL.encode() + b"|" + body, hashlib.sha256
    ).hexdigest()
    request = urllib.request.Request(  # noqa: S310 (fixed http URL from the environment)
        PUSH_TARGET,
        data=body,
        method="POST",
        headers={"Authorization": signature, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:  # noqa: S310
            return f"push delivered ({resp.status})"
    except OSError as exc:
        return f"push failed: {exc}"


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Fake Shopee</title>
<style>body{{font-family:system-ui,sans-serif;max-width:640px;margin:40px auto;padding:0 16px;
color:#222}}h1{{color:#ee4d2d}}.card{{border:1px solid #ddd;border-radius:10px;padding:16px;
margin:16px 0}}button{{background:#ee4d2d;color:#fff;border:0;border-radius:6px;padding:10px 16px;
font-size:15px;cursor:pointer}}.muted{{color:#777}}</style></head><body>{body}</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _html(self, body: str, status: int = 200) -> None:
        data = PAGE.format(body=body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def _api(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        with lock:
            fake.now = int(time.time())
            resp = fake.handle(httpx.Request(method, "http://fake" + self.path, content=body))
        self.send_response(resp.status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(resp.content)

    def do_GET(self) -> None:
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path == "/":
            notice = html.escape(query.get("msg", [""])[0])
            with lock:
                orders = len(fake.shops[SHOP_ID].orders)
            self._html(
                f"<h1>Fake Shopee (demo)</h1><p class=muted>Local stand-in for the Shopee Open "
                f"Platform. Shop <b>{SHOP_NAME}</b>, id {SHOP_ID}, {orders} orders.</p>"
                + (f"<div class=card>{notice}</div>" if notice else "")
                + "<div class=card><p>Create an order and notify the app with a signed push "
                "(webhook). The app syncs it within seconds.</p><form method=post "
                "action=/demo/new-order><button>Create a new order</button></form></div>"
            )
        elif url.path == "/api/v2/shop/auth_partner":
            redirect = query.get("redirect", [""])[0]
            self._html(
                f"<h1>Shopee</h1><div class=card><p><b>Shopee Seller Insights</b> wants to access "
                f"your shop <b>{SHOP_NAME}</b>: orders, payments (escrow) and products.</p>"
                f"<form method=get action=/demo/authorize>"
                f'<input type=hidden name=redirect value="{html.escape(redirect, quote=True)}">'
                f"<button>Authorize</button></form></div><p class=muted>Fake consent page for the "
                f"local demo.</p>"
            )
        elif url.path == "/demo/authorize":
            redirect = query.get("redirect", [""])[0]
            if not redirect.startswith(("http://localhost", "http://127.0.0.1")):
                self._html("<p>Invalid redirect.</p>", 400)
                return
            code = f"demo-code-{int(time.time() * 1000)}"
            with lock:
                fake.codes[code] = SHOP_ID
            sep = "&" if "?" in redirect else "?"
            self._redirect(f"{redirect}{sep}{urlencode({'code': code, 'shop_id': SHOP_ID})}")
        elif url.path.startswith("/api/v2/"):
            self._api("GET")
        else:
            self._html("<p>Not found.</p>", 404)

    def do_POST(self) -> None:
        if self.path == "/demo/new-order":
            with lock:
                fake.now = int(time.time())
                order_sn = new_order(fake.now, "READY_TO_SHIP")
            result = send_push(order_sn)
            self._redirect("/?" + urlencode({"msg": f"Order {order_sn} created; {result}."}))
        elif self.path.startswith("/api/v2/"):
            self._api("POST")
        else:
            self._html("<p>Not found.</p>", 404)

    def log_message(self, fmt: str, *args: object) -> None:
        # Request paths only: query strings carry access tokens.
        print(f"fake-shopee {self.command} {urlparse(self.path).path}", flush=True)


def main() -> None:
    seed()
    print(f"Fake Shopee listening on :{PORT} (shop {SHOP_ID})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()  # noqa: S104 (container)


if __name__ == "__main__":
    main()
