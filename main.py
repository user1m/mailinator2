"""
Disposable Email Service with Forwarding Capability
A Mailinator-like service built with FastAPI and aiosmtpd
"""

import asyncio
import email
import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta
from email.message import EmailMessage as StdEmailMessage
from typing import Optional

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import SMTP
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, EmailStr

# Configuration
SMTP_HOST = os.getenv("SMTP_HOST", "0.0.0.0")
SMTP_PORT = int(os.getenv("SMTP_PORT", "2525"))
WEB_PORT = int(os.getenv("WEB_PORT", "8000"))
DOMAIN = os.getenv("DOMAIN", "localhost")
MAX_EMAIL_AGE_HOURS = int(os.getenv("MAX_EMAIL_AGE_HOURS", "24"))
FORWARD_VERIFICATION_EXPIRY_HOURS = int(os.getenv("FORWARD_VERIFICATION_EXPIRY_HOURS", "24"))

# Data storage (in-memory with simple persistence)
# In production, use Redis or a proper database
emails: dict[str, list[dict]] = {}  # inbox -> list of emails
forward_rules: dict[str, dict] = {}  # inbox -> {target_email, verified, verification_token}

app = FastAPI(title="Disposable Email Service")
templates = Jinja2Templates(directory="templates")

# Ensure templates directory exists
os.makedirs("templates", exist_ok=True)


class EmailData(BaseModel):
    id: str
    from_address: str
    to_address: str
    subject: str
    body: str
    html_body: Optional[str] = None
    received_at: datetime
    forwarded: bool = False


class ForwardSetupRequest(BaseModel):
    inbox: str
    target_email: EmailStr


# SMTP Handler
class EmailHandler:
    async def handle_DATA(self, server: SMTP, session, envelope):
        """Handle incoming email data"""
        try:
            # Parse the email
            message = email.message_from_bytes(envelope.content)

            to_address = envelope.rcpt_tos[0] if envelope.rcpt_tos else ""
            from_address = envelope.mail_from or ""

            # Extract inbox from to_address (e.g., "test@domain.com" -> "test")
            inbox = to_address.split("@")[0].lower() if "@" in to_address else to_address.lower()

            # Extract email content
            subject = message.get("Subject", "")
            body = ""
            html_body = None

            if message.is_multipart():
                for part in message.walk():
                    content_type = part.get_content_type()
                    if content_type == "text/plain":
                        body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                    elif content_type == "text/html":
                        html_body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
            else:
                body = message.get_payload(decode=True).decode("utf-8", errors="ignore")

            # Create email record
            email_id = str(uuid.uuid4())
            email_data = {
                "id": email_id,
                "from_address": from_address,
                "to_address": to_address,
                "subject": subject,
                "body": body,
                "html_body": html_body,
                "received_at": datetime.now(),
                "forwarded": False,
            }

            # Store email
            if inbox not in emails:
                emails[inbox] = []
            emails[inbox].insert(0, email_data)

            # Clean old emails
            await self._clean_old_emails(inbox)

            # Check if forwarding is enabled for this inbox
            await self._forward_email(inbox, email_data)

            print(f"Received email for {inbox}: {subject}")
            return "250 Message accepted for delivery"

        except Exception as e:
            print(f"Error handling email: {e}")
            return "550 Error processing message"

    async def _clean_old_emails(self, inbox: str):
        """Remove emails older than MAX_EMAIL_AGE_HOURS"""
        cutoff = datetime.now() - timedelta(hours=MAX_EMAIL_AGE_HOURS)
        if inbox in emails:
            emails[inbox] = [e for e in emails[inbox] if e["received_at"] > cutoff]

    async def _forward_email(self, inbox: str, email_data: dict):
        """Forward email if forwarding is configured and verified"""
        if inbox in forward_rules:
            rule = forward_rules[inbox]
            if rule.get("verified") and rule.get("active"):
                try:
                    await self._send_forward_email(rule["target_email"], email_data, inbox)
                    email_data["forwarded"] = True
                    print(f"Forwarded email from {inbox} to {rule['target_email']}")
                except Exception as e:
                    print(f"Failed to forward email: {e}")

    async def _send_forward_email(self, target_email: str, email_data: dict, inbox: str):
        """Actually send the forwarded email"""
        import aiosmtplib

        # Create forwarded email
        msg = StdEmailMessage()
        msg["From"] = f"forwarder@{DOMAIN}"
        msg["To"] = target_email
        msg["Subject"] = f"[Forwarded from {inbox}@{DOMAIN}] {email_data['subject']}"

        # Build body with notice
        forward_notice = f"""
---
This email was forwarded from disposable inbox: {inbox}@{DOMAIN}
Original sender: {email_data['from_address']}
Original recipient: {email_data['to_address']}
Received: {email_data['received_at'].strftime('%Y-%m-%d %H:%M:%S')}
---

"""

        if email_data.get("html_body"):
            msg.add_alternative(
                f"<html><body><p><em>{forward_notice.replace(chr(10), '<br>')}</em></p>{email_data['html_body']}</body></html>",
                subtype="html",
            )
        else:
            msg.set_content(forward_notice + email_data["body"])

        # Send via local SMTP (you'd configure your SMTP server here)
        # For now, just log it - configure with your SMTP credentials
        print(f"Would forward to {target_email}:")
        print(f"Subject: {msg['Subject']}")

        # Example with aiosmtplib (configure with your SMTP server):
        # await aiosmtplib.send(
        #     msg,
        #     hostname="smtp.gmail.com",
        #     port=587,
        #     username="your-email@gmail.com",
        #     password="your-password",
        #     start_tls=True,
        # )


# Web Routes
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Home page"""
    return templates.TemplateResponse("index.html", {"request": request, "domain": DOMAIN})


@app.get("/inbox/{inbox_name}", response_class=HTMLResponse)
async def view_inbox(request: Request, inbox_name: str):
    """View inbox contents"""
    inbox_emails = emails.get(inbox_name.lower(), [])
    forward_rule = forward_rules.get(inbox_name.lower())

    return templates.TemplateResponse(
        "inbox.html",
        {
            "request": request,
            "inbox": inbox_name.lower(),
            "emails": inbox_emails,
            "domain": DOMAIN,
            "forward_rule": forward_rule,
        },
    )


@app.get("/inbox/{inbox_name}/email/{email_id}", response_class=HTMLResponse)
async def view_email(request: Request, inbox_name: str, email_id: str):
    """View a single email"""
    inbox_emails = emails.get(inbox_name.lower(), [])
    email_data = next((e for e in inbox_emails if e["id"] == email_id), None)

    if not email_data:
        raise HTTPException(status_code=404, detail="Email not found")

    return templates.TemplateResponse(
        "email.html",
        {"request": request, "inbox": inbox_name.lower(), "email": email_data, "domain": DOMAIN},
    )


@app.post("/api/inbox/{inbox_name}/forward")
async def setup_forwarding(inbox_name: str, target_email: str = Form(...)):
    """Set up email forwarding for an inbox (requires verification)"""
    inbox = inbox_name.lower()

    # Generate verification token
    verification_token = hashlib.sha256(f"{inbox}:{target_email}:{uuid.uuid4()}".encode()).hexdigest()

    # Store forwarding rule (unverified)
    forward_rules[inbox] = {
        "target_email": target_email,
        "verified": False,
        "verification_token": verification_token,
        "created_at": datetime.now(),
        "expires_at": datetime.now() + timedelta(hours=FORWARD_VERIFICATION_EXPIRY_HOURS),
        "active": False,
    }

    # Generate verification URL
    verification_url = f"http://{DOMAIN}:{WEB_PORT}/verify/{inbox}/{verification_token}"

    # In a real implementation, send this via email
    # For now, return it in the response (for demo purposes)
    print(f"\n{'='*60}")
    print(f"VERIFICATION REQUIRED for {inbox}@{DOMAIN}")
    print(f"Target email: {target_email}")
    print(f"Verification URL: {verification_url}")
    print(f"{'='*60}\n")

    return {
        "status": "pending_verification",
        "message": "Please check your email and click the verification link to activate forwarding.",
        "verification_url": verification_url,  # Remove in production - for demo only
    }


@app.get("/verify/{inbox}/{token}")
async def verify_forwarding(inbox: str, token: str):
    """Verify and activate email forwarding"""
    inbox = inbox.lower()

    if inbox not in forward_rules:
        raise HTTPException(status_code=404, detail="Forwarding rule not found")

    rule = forward_rules[inbox]

    if rule["verification_token"] != token:
        raise HTTPException(status_code=400, detail="Invalid verification token")

    if datetime.now() > rule["expires_at"]:
        raise HTTPException(status_code=400, detail="Verification link has expired")

    if rule["verified"]:
        return {"status": "already_verified", "message": "Forwarding is already active"}

    # Activate forwarding
    rule["verified"] = True
    rule["active"] = True
    rule["verified_at"] = datetime.now()

    return {
        "status": "verified",
        "message": f"Email forwarding activated! Emails to {inbox}@{DOMAIN} will now be forwarded to {rule['target_email']}",
    }


@app.post("/api/inbox/{inbox_name}/forward/disable")
async def disable_forwarding(inbox_name: str):
    """Disable email forwarding for an inbox"""
    inbox = inbox_name.lower()

    if inbox in forward_rules:
        forward_rules[inbox]["active"] = False
        return {"status": "disabled", "message": "Email forwarding has been disabled"}

    raise HTTPException(status_code=404, detail="No forwarding rule found")


@app.post("/api/inbox/{inbox_name}/forward/delete")
async def delete_forwarding(inbox_name: str):
    """Delete email forwarding rule for an inbox (POST for HTML form support)"""
    inbox = inbox_name.lower()

    if inbox in forward_rules:
        del forward_rules[inbox]
        return RedirectResponse(url=f"/inbox/{inbox}", status_code=303)

    raise HTTPException(status_code=404, detail="No forwarding rule found")


@app.get("/api/inbox/{inbox_name}/emails")
async def get_emails_json(inbox_name: str):
    """Get inbox emails as JSON (API endpoint)"""
    inbox_emails = emails.get(inbox_name.lower(), [])
    return {"inbox": inbox_name.lower(), "emails": inbox_emails}


@app.get("/api/stats")
async def get_stats():
    """Get service statistics"""
    total_inboxes = len(emails)
    total_emails = sum(len(inbox_emails) for inbox_emails in emails.values())
    active_forward_rules = sum(1 for rule in forward_rules.values() if rule.get("active"))

    return {
        "total_inboxes": total_inboxes,
        "total_emails": total_emails,
        "active_forward_rules": active_forward_rules,
    }


# Create HTML templates
def create_templates():
    """Create HTML template files"""
    os.makedirs("templates", exist_ok=True)

    # Base template
    base_template = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}Disposable Email{% endblock %}</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f5f5f5;
            color: #333;
            line-height: 1.6;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
        }
        header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px 0;
            text-align: center;
            margin-bottom: 30px;
        }
        header h1 { font-size: 2.5rem; margin-bottom: 10px; }
        header p { opacity: 0.9; }
        .card {
            background: white;
            border-radius: 12px;
            padding: 25px;
            margin-bottom: 20px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        .btn {
            display: inline-block;
            padding: 12px 24px;
            background: #667eea;
            color: white;
            text-decoration: none;
            border-radius: 6px;
            border: none;
            cursor: pointer;
            font-size: 16px;
            transition: background 0.3s;
        }
        .btn:hover { background: #5568d3; }
        .btn-secondary { background: #48bb78; }
        .btn-secondary:hover { background: #38a169; }
        .btn-danger { background: #e53e3e; }
        .btn-danger:hover { background: #c53030; }
        input[type="text"], input[type="email"] {
            width: 100%;
            padding: 12px;
            border: 2px solid #e2e8f0;
            border-radius: 6px;
            font-size: 16px;
            margin-bottom: 10px;
        }
        input:focus { outline: none; border-color: #667eea; }
        .email-list { list-style: none; }
        .email-item {
            padding: 20px;
            border-bottom: 1px solid #e2e8f0;
            cursor: pointer;
            transition: background 0.2s;
        }
        .email-item:hover { background: #f7fafc; }
        .email-item:last-child { border-bottom: none; }
        .email-subject { font-weight: 600; color: #2d3748; margin-bottom: 5px; }
        .email-meta { font-size: 14px; color: #718096; }
        .badge {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 600;
        }
        .badge-success { background: #c6f6d5; color: #22543d; }
        .badge-warning { background: #fefcbf; color: #744210; }
        .badge-info { background: #bee3f8; color: #2a4365; }
        .alert {
            padding: 15px;
            border-radius: 6px;
            margin-bottom: 20px;
        }
        .alert-success { background: #c6f6d5; color: #22543d; }
        .alert-info { background: #bee3f8; color: #2a4365; }
        .alert-warning { background: #fefcbf; color: #744210; }
        .forward-form {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            align-items: flex-end;
        }
        .forward-form input { flex: 1; min-width: 250px; margin-bottom: 0; }
        .email-body {
            background: #f7fafc;
            padding: 20px;
            border-radius: 8px;
            margin-top: 20px;
            white-space: pre-wrap;
        }
    </style>
</head>
<body>
    {% block content %}{% endblock %}
</body>
</html>'''

    # Index template
    index_template = '''{% extends "base.html" %}

{% block title %}Disposable Email Service{% endblock %}

{% block content %}
<header>
    <h1>Disposable Email Service</h1>
    <p>Create temporary email inboxes instantly. Forward important emails to your real address.</p>
</header>

<div class="container">
    <div class="card">
        <h2>Create an Inbox</h2>
        <p>Enter any inbox name to create or access it:</p>
        <form onsubmit="goToInbox(event)">
            <input type="text" id="inboxName" placeholder="your-inbox-name" required>
            <button type="submit" class="btn">Open Inbox</button>
        </form>
        <p style="margin-top: 15px; color: #718096;">
            Your inbox will be: <strong>your-inbox-name@{{ domain }}</strong>
        </p>
    </div>

    <div class="card">
        <h3>Features</h3>
        <ul style="margin-left: 20px; line-height: 2;">
            <li><strong>Instant inboxes</strong> - No registration required</li>
            <li><strong>Email forwarding</strong> - Forward important emails to your real address</li>
            <li><strong>Auto-cleanup</strong> - Emails automatically deleted after 24 hours</li>
            <li><strong>Privacy focused</strong> - No personal data stored</li>
        </ul>
    </div>
</div>

<script>
function goToInbox(event) {
    event.preventDefault();
    const inbox = document.getElementById('inboxName').value.trim().toLowerCase();
    if (inbox) {
        window.location.href = '/inbox/' + inbox;
    }
}
</script>
{% endblock %}'''

    # Inbox template
    inbox_template = '''{% extends "base.html" %}

{% block title %}{{ inbox }}@{{ domain }} - Inbox{% endblock %}

{% block content %}
<header>
    <h1>{{ inbox }}@{{ domain }}</h1>
    <p>Temporary Email Inbox</p>
</header>

<div class="container">
    <a href="/" class="btn" style="margin-bottom: 20px;">← Back to Home</a>

    <!-- Forwarding Section -->
    <div class="card">
        <h3>Email Forwarding</h3>

        {% if forward_rule and forward_rule.verified and forward_rule.active %}
            <div class="alert alert-success">
                <strong>Forwarding Active</strong><br>
                Emails are being forwarded to: {{ forward_rule.target_email }}
            </div>
            <form action="/api/inbox/{{ inbox }}/forward/disable" method="post" style="display: inline;">
                <button type="submit" class="btn btn-danger">Disable Forwarding</button>
            </form>
            <form action="/api/inbox/{{ inbox }}/forward/delete" method="post" style="display: inline; margin-left: 10px;">
                <button type="submit" class="btn btn-danger">Remove Forwarding Rule</button>
            </form>

        {% elif forward_rule and not forward_rule.verified %}
            <div class="alert alert-warning">
                <strong>Verification Pending</strong><br>
                Please check {{ forward_rule.target_email }} and click the verification link to activate forwarding.
                <br><small>Link expires in 24 hours</small>
            </div>

        {% else %}
            <p>Forward emails from this inbox to your real email address:</p>
            <form action="/api/inbox/{{ inbox }}/forward" method="post" class="forward-form">
                <input type="email" name="target_email" placeholder="your-real-email@example.com" required>
                <button type="submit" class="btn btn-secondary">Set Up Forwarding</button>
            </form>
            <p style="margin-top: 10px; font-size: 14px; color: #718096;">
                You'll need to verify your email address before forwarding begins.
            </p>
        {% endif %}
    </div>

    <!-- Email List -->
    <div class="card">
        <h3>Inbox Contents</h3>

        {% if emails %}
            <ul class="email-list">
                {% for email in emails %}
                <li class="email-item" onclick="window.location.href='/inbox/{{ inbox }}/email/{{ email.id }}'">
                    <div class="email-subject">
                        {{ email.subject or "(No Subject)" }}
                        {% if email.forwarded %}
                            <span class="badge badge-success" style="margin-left: 10px;">Forwarded</span>
                        {% endif %}
                    </div>
                    <div class="email-meta">
                        From: {{ email.from_address }} |
                        Received: {{ email.received_at.strftime('%Y-%m-%d %H:%M:%S') }}
                    </div>
                </li>
                {% endfor %}
            </ul>
        {% else %}
            <p style="color: #718096; text-align: center; padding: 40px;">
                No emails yet.<br>
                Send an email to <strong>{{ inbox }}@{{ domain }}</strong> to see it appear here.
            </p>
        {% endif %}
    </div>

    <div style="text-align: center; color: #718096; font-size: 14px;">
        <p>Emails are automatically deleted after 24 hours</p>
    </div>
</div>
{% endblock %}'''

    # Email view template
    email_template = '''{% extends "base.html" %}

{% block title %}{{ email.subject or "No Subject" }} - {{ inbox }}{% endblock %}

{% block content %}
<header>
    <h1>{{ inbox }}@{{ domain }}</h1>
    <p>Viewing Email</p>
</header>

<div class="container">
    <a href="/inbox/{{ inbox }}" class="btn" style="margin-bottom: 20px;">← Back to Inbox</a>

    <div class="card">
        <h2>{{ email.subject or "(No Subject)" }}</h2>

        <div style="margin: 20px 0; padding: 15px; background: #f7fafc; border-radius: 8px;">
            <p><strong>From:</strong> {{ email.from_address }}</p>
            <p><strong>To:</strong> {{ email.to_address }}</p>
            <p><strong>Received:</strong> {{ email.received_at.strftime('%Y-%m-%d %H:%M:%S') }}</p>
            {% if email.forwarded %}
                <span class="badge badge-success">Forwarded</span>
            {% endif %}
        </div>

        <div class="email-body">
            {% if email.html_body %}
                <div>{{ email.html_body | safe }}</div>
            {% else %}
                {{ email.body }}
            {% endif %}
        </div>
    </div>
</div>
{% endblock %}'''

    with open("templates/base.html", "w") as f:
        f.write(base_template)

    with open("templates/index.html", "w") as f:
        f.write(index_template)

    with open("templates/inbox.html", "w") as f:
        f.write(inbox_template)

    with open("templates/email.html", "w") as f:
        f.write(email_template)

    print("Templates created successfully!")


# Main entry point
async def main():
    """Start both SMTP and HTTP servers"""
    print("=" * 60)
    print("Disposable Email Service with Forwarding")
    print("=" * 60)

    # Create templates
    create_templates()

    # Start SMTP server
    handler = EmailHandler()
    smtp_controller = Controller(handler, hostname=SMTP_HOST, port=SMTP_PORT)
    smtp_controller.start()
    print(f"\nSMTP Server running on {SMTP_HOST}:{SMTP_PORT}")
    print(f"Send emails to: anything@{DOMAIN}")

    # Start HTTP server
    import uvicorn

    print(f"\nWeb UI: http://localhost:{WEB_PORT}")
    print(f"Example inbox: http://localhost:{WEB_PORT}/inbox/test")
    print("\nPress Ctrl+C to stop\n")

    config = uvicorn.Config(app, host="0.0.0.0", port=WEB_PORT, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

    # Cleanup
    smtp_controller.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutting down...")
