"""Weekly e-mail digest: the week's numbers in plain text, plus the AI summary if enabled.

The worker calls `send_due_digests` regularly; a shop gets at most one digest every
7 days, sent to its owners and managers, and only if it opted in (Settings).
"""

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.mailer import MailError, send_email
from app.models import Membership, Seller, User
from app.services import ai_summary

log = logging.getLogger(__name__)
DIGEST_EVERY_DAYS = 7
ALERT_LABELS = {
    "low_stock": "Low stock",
    "stalled_product": "Stalled products",
    "low_margin": "Low margin",
    "high_returns": "High returns",
}


def _money(value: Any) -> str:
    return f"R$ {Decimal(str(value)):,.2f}"


def _change(pct: float | None) -> str:
    if pct is None:
        return "no data for the previous week"
    arrow = "up" if pct > 0 else "down" if pct < 0 else "flat"
    return f"{arrow} {abs(pct):.1f}% vs previous week"


def render_digest(shop_name: str, facts: dict[str, Any], ai_text: str | None) -> str:
    week = facts["this_week"]
    change = facts["change_pct"]
    lines = [
        f"Weekly summary for {shop_name}",
        f"Period: {facts['period']['start']} to {facts['period']['end']}",
        "",
        f"Revenue:        {_money(week['revenue'])} ({_change(change['revenue_pct'])})",
        f"Orders:         {week['orders']} ({_change(change['orders_pct'])})",
        f"Average ticket: {_money(week['avg_ticket'])} ({_change(change['avg_ticket_pct'])})",
        "Net margin:     "
        + (_money(week["net_margin"]) if week["net_margin"] is not None else "set product costs"),
        "",
        "Top products:",
    ]
    if facts["top_products"]:
        for p in facts["top_products"]:
            margin = f", margin {p['margin_pct']:.1f}%" if p["margin_pct"] is not None else ""
            lines.append(f"  - {p['name']}: {p['units']} units, {_money(p['revenue'])}{margin}")
    else:
        lines.append("  - no sales this week")
    lines += ["", "Alerts:"]
    any_alert = False
    for kind, names in facts["alerts"].items():
        if names:
            any_alert = True
            lines.append(f"  - {ALERT_LABELS.get(kind, kind)}: {', '.join(names)}")
    if not any_alert:
        lines.append("  - nothing needs your attention")
    if ai_text:
        lines += ["", "AI summary:", ai_text]
    lines += [
        "",
        f"Open the dashboard: {get_settings().app_base_url}",
        "You receive this because weekly e-mails are enabled in the shop's Settings.",
    ]
    return "\n".join(lines)


def build_digest(db: Session, seller: Seller, today: date) -> tuple[str, str]:
    facts = ai_summary.weekly_facts(db, seller.id, today)
    ai_text = None
    if ai_summary.ai_enabled():
        try:
            ai_text = ai_summary.generate_summary(facts)
        except ai_summary.SummaryUnavailableError:
            ai_text = None  # the numbers alone are still useful
    subject = (
        f"{seller.name}: weekly summary ({facts['period']['start']} to {facts['period']['end']})"
    )
    return subject, render_digest(seller.name, facts, ai_text)


def recipients(db: Session, seller_id: int) -> list[str]:
    return list(
        db.scalars(
            select(User.email)
            .join(Membership, Membership.user_id == User.id)
            .where(
                Membership.seller_id == seller_id,
                Membership.role.in_(("owner", "manager")),
            )
            .order_by(User.email)
        )
    )


def send_due_digests(db: Session, today: date) -> int:
    """Send every digest that is due; returns how many shops were e-mailed."""
    if not get_settings().email_enabled:
        return 0
    due = db.scalars(
        select(Seller).where(
            Seller.weekly_email.is_(True),
            or_(
                Seller.last_digest_sent_on.is_(None),
                Seller.last_digest_sent_on <= today - timedelta(days=DIGEST_EVERY_DAYS),
            ),
        )
    ).all()
    sent = 0
    for seller in due:
        to = recipients(db, seller.id)
        if not to:
            continue
        try:
            subject, body = build_digest(db, seller, today)
            send_email(to, subject, body)
        except MailError:
            continue  # retried on the next worker cycle
        seller.last_digest_sent_on = today
        db.commit()
        sent += 1
    if sent:
        log.info("weekly digests sent: %s", sent)
    return sent
