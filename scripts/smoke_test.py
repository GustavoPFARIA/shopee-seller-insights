#!/usr/bin/env python3
"""End-to-end smoke test against a running docker compose stack.

It behaves like a real user and a curious attacker would, through nginx only:

    python3 scripts/smoke_test.py [--base-url http://localhost:8080]

Run it against a freshly started stack (``scripts/verify.sh e2e`` does that).
It uses only the Python standard library so it runs on any machine with
Python 3.10+. Exit code 0 means every check passed.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_CSV = ROOT / "docs" / "sample-orders.csv"
DEMO_PASSWORD = "DemoPassword123!"  # noqa: S105 (public demo account from the README)
OWNER = "demo@shopee-insights.dev"
VIEWER = "viewer@shopee-insights.dev"
OTHER = "other@shopee-insights.dev"
CSRF = {"X-Requested-With": "ssi"}


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body)

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


@dataclass
class Browser:
    """A minimal browser: its own cookie jar and, once signed in, an access token."""

    base_url: str
    jar: http.cookiejar.CookieJar = field(default_factory=http.cookiejar.CookieJar)
    token: str | None = None

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        auth: bool = True,
    ) -> Response:
        all_headers = dict(headers or {})
        if auth and self.token:
            all_headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(  # noqa: S310 (http(s) only, see main)
            self.base_url + path, data=body, headers=all_headers, method=method
        )
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), NoRedirect()
        )
        try:
            with opener.open(req, timeout=60) as resp:
                return Response(
                    resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
                )
        except urllib.error.HTTPError as err:
            return Response(err.code, {k.lower(): v for k, v in err.headers.items()}, err.read())

    def get(self, path: str, **kw: Any) -> Response:
        return self.request("GET", path, **kw)

    def post_json(self, path: str, payload: dict[str, Any], **kw: Any) -> Response:
        headers = {"Content-Type": "application/json", **kw.pop("headers", {})}
        return self.request("POST", path, body=json.dumps(payload).encode(), headers=headers, **kw)

    def post_form(self, path: str, form: dict[str, str]) -> Response:
        body = urllib.parse.urlencode(form).encode()
        return self.request(
            "POST", path, body=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
        )

    def upload(self, path: str, filename: str, content: bytes) -> Response:
        boundary = uuid.uuid4().hex
        body = (
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                f'filename="{filename}"\r\nContent-Type: text/csv\r\n\r\n'
            ).encode()
            + content
            + f"\r\n--{boundary}--\r\n".encode()
        )
        return self.request(
            "POST",
            path,
            body=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )

    def login(self, email: str, password: str = DEMO_PASSWORD) -> Response:
        resp = self.post_form("/api/auth/login", {"username": email, "password": password})
        if resp.status == 200:
            self.token = resp.json()["access_token"]
        return resp


class Checks:
    def __init__(self) -> None:
        self.passed = 0
        self.failed: list[str] = []

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.passed += 1
            print(f"  PASS  {name}")
        else:
            self.failed.append(name)
            print(f"  FAIL  {name}  {detail}")


def wait_until_ready(base_url: str, timeout: float = 180) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            # Scheme validated in main(): only http(s) URLs reach here.
            with urllib.request.urlopen(base_url + "/api/health", timeout=5) as resp:  # noqa: S310
                if resp.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(2)
    raise SystemExit(f"Stack not ready at {base_url} after {timeout:.0f}s")


def port_is_open(host: str, port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex((host, port)) == 0


def run(base_url: str) -> Checks:
    c = Checks()
    host = urllib.parse.urlparse(base_url).hostname or "localhost"
    owner = Browser(base_url)

    print("\n[1] Serving and HTTP security headers")
    home = owner.get("/", auth=False)
    c.check("web app is served", home.status == 200 and b'<div id="root">' in home.body)
    csp = home.headers.get("content-security-policy", "")
    c.check(
        "strict Content-Security-Policy",
        "script-src 'self'" in csp and "frame-ancestors 'none'" in csp,
        csp,
    )
    c.check(
        "X-Content-Type-Options nosniff", home.headers.get("x-content-type-options") == "nosniff"
    )
    c.check("Referrer-Policy no-referrer", home.headers.get("referrer-policy") == "no-referrer")
    c.check("nginx hides its version", home.headers.get("server", "") == "nginx")
    c.check(
        "SPA routes fall back to index.html",
        owner.get("/some/client/route", auth=False).status == 200,
    )
    c.check(
        "API health through nginx", owner.get("/api/health", auth=False).json() == {"status": "ok"}
    )

    print("\n[2] Network exposure")
    c.check("PostgreSQL port 5432 is not published", not port_is_open(host, 5432))
    c.check("API port 8000 is not published", not port_is_open(host, 8000))

    print("\n[3] Authentication and sessions")
    c.check(
        "protected endpoint without token -> 401", owner.get("/api/metrics/overview").status == 401
    )
    c.check(
        "wrong password -> 401", Browser(base_url).login(OWNER, "wrong-password-1").status == 401
    )
    resp = owner.login(OWNER)
    c.check("demo owner can sign in", resp.status == 200, resp.text[:200])
    cookie = resp.headers.get("set-cookie", "").lower()
    c.check(
        "refresh cookie is HttpOnly, SameSite=Strict, scoped to /api/auth",
        "httponly" in cookie and "samesite=strict" in cookie and "path=/api/auth" in cookie,
        cookie,
    )
    c.check("refresh token is not in the response body", "ssi_refresh" not in resp.text)
    c.check(
        "refresh without CSRF header -> 403",
        owner.request("POST", "/api/auth/refresh").status == 403,
    )
    refreshed = owner.request("POST", "/api/auth/refresh", headers=CSRF)
    c.check("refresh with cookie + CSRF header -> new access token", refreshed.status == 200)
    if refreshed.status == 200:
        owner.token = refreshed.json()["access_token"]
    me = owner.get("/api/auth/me").json()
    c.check("role is owner", me.get("role") == "owner", str(me))

    print("\n[4] Seeded demo data and metrics")
    overview = owner.get("/api/metrics/overview")
    c.check("overview loads", overview.status == 200)
    cur = overview.json().get("current", {}) if overview.status == 200 else {}
    c.check("demo data is present (orders > 0)", cur.get("orders", 0) > 0, str(cur))
    c.check("money is serialized as decimal strings", isinstance(cur.get("revenue"), str))
    for path in ("daily", "products", "abc"):
        r = owner.get(f"/api/metrics/{path}")
        c.check(f"/api/metrics/{path} returns data", r.status == 200 and len(r.json()) > 0)
    abc_classes = {i["abc_class"] for i in owner.get("/api/metrics/abc").json()}
    c.check("ABC curve has classes A, B and C", abc_classes == {"A", "B", "C"}, str(abc_classes))
    alerts = owner.get("/api/alerts").json()
    c.check("alerts are produced", len(alerts) > 0)
    c.check(
        "invalid period is rejected",
        owner.get("/api/metrics/overview?start=2025-09-30&end=2025-09-01").status == 422,
    )

    print("\n[5] Upload: validation, idempotency and limits")
    sample = SAMPLE_CSV.read_bytes()
    first = owner.upload("/api/uploads", "sample-orders.csv", sample)
    c.check("sample export uploads", first.status == 201, first.text[:300])
    second = owner.upload("/api/uploads", "sample-orders.csv", sample)
    body2 = second.json() if second.status == 201 else {}
    c.check(
        "re-upload is idempotent (0 new orders)",
        second.status == 201
        and body2.get("orders_created") == 0
        and body2.get("orders_unchanged", 0) > 0,
        str(body2),
    )
    evil = (
        "ID do pedido,Status do pedido,Data de criação do pedido,Número de referência SKU,"
        "Nome do Produto,Preço acordado,Quantidade\n"
        'X1,Concluído,2025-09-01 10:00,SKU-1,"=HYPERLINK(""http://evil"")",10,1\n'
    ).encode()
    bad = owner.upload("/api/uploads", "evil.csv", evil)
    c.check("formula injection in a cell is rejected (422)", bad.status == 422, bad.text[:200])
    c.check(
        "disguised binary is rejected",
        owner.upload("/api/uploads", "orders.xlsx", b"MZ\x90\x00binary").status == 422,
    )
    big = owner.upload("/api/uploads", "big.csv", b"a" * (5 * 1024 * 1024 + 10))
    c.check("upload above 5 MB is rejected (413)", big.status == 413, str(big.status))
    export = owner.get("/api/metrics/products/export.csv?start=2025-01-01&end=2025-12-31")
    c.check(
        "CSV export is an attachment",
        export.status == 200 and "attachment" in export.headers.get("content-disposition", ""),
    )

    print("\n[6] Tenant isolation and roles")
    other = Browser(base_url)
    other.login(OTHER)
    mine = {p["sku"] for p in owner.get("/api/products").json()}
    theirs = {p["sku"] for p in other.get("/api/products").json()}
    other_overview = other.get("/api/metrics/overview").json()["current"]
    c.check(
        "another shop does not see the demo shop's revenue",
        other_overview["revenue"] != cur.get("revenue"),
    )
    some_product = owner.get("/api/products").json()[0]["id"]
    c.check(
        "another shop cannot edit the demo shop's product (404)",
        other.request(
            "PATCH",
            f"/api/products/{some_product}",
            body=b'{"unit_cost": "1.00"}',
            headers={"Content-Type": "application/json"},
        ).status
        == 404,
    )
    c.check("product catalogues are separate", mine != theirs)
    viewer = Browser(base_url)
    viewer.login(VIEWER)
    c.check("viewer can read metrics", viewer.get("/api/metrics/overview").status == 200)
    c.check(
        "viewer cannot upload (403)", viewer.upload("/api/uploads", "s.csv", sample).status == 403
    )
    c.check(
        "viewer cannot invite (403)",
        viewer.post_json(
            "/api/members/invitations", {"email": "x@example.com", "role": "owner"}
        ).status
        == 403,
    )

    print("\n[7] Optional integrations degrade gracefully")
    summary = owner.get("/api/summary/weekly")
    c.check("AI summary works or reports disabled", summary.status == 200, summary.text[:200])
    shopee = owner.get("/api/shopee/status").json()
    c.check("Shopee status endpoint answers", "enabled" in shopee and "connected" in shopee)

    print("\n[8] Logout and rate limiting")
    out = owner.request("POST", "/api/auth/logout", headers=CSRF)
    c.check("logout -> 204", out.status == 204)
    c.check(
        "refresh after logout -> 401",
        owner.request("POST", "/api/auth/refresh", headers=CSRF).status == 401,
    )
    attacker = Browser(base_url)
    codes = []
    for i in range(8):
        r = attacker.request(
            "POST",
            "/api/auth/login",
            body=urllib.parse.urlencode(
                {"username": f"x{i}@example.com", "password": "wrong-pass-1"}
            ).encode(),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Forwarded-For": f"10.0.0.{i}",  # spoofing must not help
            },
        )
        codes.append(r.status)
    c.check(
        "brute force is rate limited (429) despite spoofed X-Forwarded-For",
        429 in codes,
        str(codes),
    )
    return c


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8080")
    args = parser.parse_args()
    if urllib.parse.urlparse(args.base_url).scheme not in ("http", "https"):
        raise SystemExit("--base-url must be an http(s) URL")
    wait_until_ready(args.base_url)
    checks = run(args.base_url.rstrip("/"))
    total = checks.passed + len(checks.failed)
    print(f"\n{checks.passed}/{total} smoke checks passed")
    if checks.failed:
        print("Failed: " + "; ".join(checks.failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
