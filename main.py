"""
Disposable Email Service with Forwarding Capability
A Mailinator-like service built with FastAPI and aiosmtpd
"""

import asyncio
import email
import os
import random
import uuid
from datetime import datetime, timedelta
from email.message import EmailMessage as StdEmailMessage
from typing import Optional

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import SMTP
from dotenv import load_dotenv
from fastapi import FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, EmailStr

# Load environment variables from .env file
load_dotenv()

async def send_forward_email(target_email: str, email_data: dict, inbox: str):
    """Send the forwarded email to the target address"""
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


# Configuration
# Railway provides PORT env var - use it if available
WEB_PORT = int(os.getenv("PORT", os.getenv("WEB_PORT", "8000")))
SMTP_HOST = os.getenv("SMTP_HOST", "0.0.0.0")
SMTP_PORT = int(os.getenv("SMTP_PORT", "2525"))
DOMAIN = os.getenv("DOMAIN", "localhost")
RAILWAY_STATIC_URL = os.getenv("RAILWAY_STATIC_URL", "")
MAX_EMAIL_AGE_HOURS = int(os.getenv("MAX_EMAIL_AGE_HOURS", "24"))
FORWARD_VERIFICATION_EXPIRY_HOURS = int(os.getenv("FORWARD_VERIFICATION_EXPIRY_HOURS", "24"))

# Production mode detection
IS_PRODUCTION = os.getenv("RAILWAY_ENVIRONMENT") == "production" or os.getenv("PRODUCTION") == "true"

# Resend Configuration
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_WEBHOOK_SECRET = os.getenv("RESEND_WEBHOOK_SECRET", "")
RESEND_DOMAIN = os.getenv("RESEND_DOMAIN", DOMAIN)  # Domain configured in Resend

# Data storage (in-memory with simple persistence)
# In production, use Redis or a proper database
emails: dict[str, list[dict]] = {}  # inbox -> list of emails

# Per-email forward requests with verification codes
# Key: f"{inbox}:{email_id}", Value: {target_email, verification_code, expires_at, verified}
forward_requests: dict[str, dict] = {}

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


# Web Routes
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Home page"""
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "domain": DOMAIN,
            "resend_domain": RESEND_DOMAIN if RESEND_API_KEY else None,
        }
    )


@app.get("/inbox/{inbox_name}", response_class=HTMLResponse)
async def view_inbox(request: Request, inbox_name: str):
    """View inbox contents"""
    inbox = inbox_name.lower()
    inbox_emails = emails.get(inbox, [])

    return templates.TemplateResponse(
        "inbox.html",
        {
            "request": request,
            "inbox": inbox,
            "emails": inbox_emails,
            "domain": DOMAIN,
        },
    )


@app.get("/inbox/{inbox_name}/email/{email_id}", response_class=HTMLResponse)
async def view_email(request: Request, inbox_name: str, email_id: str):
    """View a single email"""
    inbox = inbox_name.lower()
    inbox_emails = emails.get(inbox, [])
    email_data = next((e for e in inbox_emails if e["id"] == email_id), None)

    if not email_data:
        raise HTTPException(status_code=404, detail="Email not found")

    # Check if there's a pending forward request for this email
    request_key = f"{inbox}:{email_id}"
    forward_request = forward_requests.get(request_key)

    return templates.TemplateResponse(
        "email.html",
        {
            "request": request,
            "inbox": inbox,
            "email": email_data,
            "domain": DOMAIN,
            "forward_request": forward_request,
        },
    )


def generate_verification_code() -> str:
    """Generate a 6-digit verification code"""
    import random
    return ''.join(random.choices('0123456789', k=6))


async def send_verification_email(target_email: str, code: str, email_subject: str):
    """Send verification code to target email (logs to console for demo)"""
    print(f"\n{'='*60}")
    print(f"VERIFICATION CODE for {target_email}")
    print(f"Code: {code}")
    print(f"Email Subject: {email_subject}")
    print(f"{'='*60}\n")
    # In production, actually send email via SMTP


@app.post("/api/inbox/{inbox_name}/email/{email_id}/forward-request")
async def request_email_forward(inbox_name: str, email_id: str, target_email: str = Form(...)):
    """Request to forward a specific email - sends verification code"""
    inbox = inbox_name.lower()

    # Find the email
    inbox_emails = emails.get(inbox, [])
    email_data = next((e for e in inbox_emails if e["id"] == email_id), None)

    if not email_data:
        raise HTTPException(status_code=404, detail="Email not found")

    # Generate verification code
    verification_code = generate_verification_code()

    # Store forward request
    request_key = f"{inbox}:{email_id}"
    forward_requests[request_key] = {
        "target_email": target_email,
        "verification_code": verification_code,
        "created_at": datetime.now(),
        "expires_at": datetime.now() + timedelta(minutes=15),  # 15 minute expiry
        "verified": False,
    }

    # "Send" verification code
    await send_verification_email(target_email, verification_code, email_data.get("subject", "(No Subject)"))

    return {
        "status": "verification_sent",
        "message": f"A verification code has been sent to {target_email}. Please enter the code to complete forwarding.",
        "demo_code": verification_code,  # Remove in production - for demo only
    }


@app.post("/api/inbox/{inbox_name}/email/{email_id}/forward-verify")
async def verify_and_forward_email(
    inbox_name: str,
    email_id: str,
    verification_code: str = Form(...),
    target_email: str = Form(...)
):
    """Verify the code and forward the email"""
    inbox = inbox_name.lower()
    request_key = f"{inbox}:{email_id}"

    if request_key not in forward_requests:
        raise HTTPException(status_code=404, detail="Forward request not found. Please request forwarding again.")

    request = forward_requests[request_key]

    # Check expiry
    if datetime.now() > request["expires_at"]:
        del forward_requests[request_key]
        raise HTTPException(status_code=400, detail="Verification code has expired. Please request forwarding again.")

    # Verify code
    if request["verification_code"] != verification_code:
        raise HTTPException(status_code=400, detail="Invalid verification code. Please try again.")

    # Find the email
    inbox_emails = emails.get(inbox, [])
    email_data = next((e for e in inbox_emails if e["id"] == email_id), None)

    if not email_data:
        raise HTTPException(status_code=404, detail="Email not found")

    # Forward the email
    try:
        await send_forward_email(target_email, email_data, inbox)
        email_data["forwarded"] = True

        # Clean up the request
        del forward_requests[request_key]

        return {
            "status": "forwarded",
            "message": f"Email successfully forwarded to {target_email}",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to forward email: {str(e)}")


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
    forwarded_emails = sum(
        sum(1 for e in inbox_emails if e.get("forwarded"))
        for inbox_emails in emails.values()
    )

    return {
        "total_inboxes": total_inboxes,
        "total_emails": total_emails,
        "forwarded_emails": forwarded_emails,
    }


@app.post("/api/inbox/{inbox_name}/receive")
async def receive_email_api(
    inbox_name: str,
    from_address: str = Form(...),
    subject: str = Form(""),
    body: str = Form(""),
    html_body: Optional[str] = Form(None),
):
    """Receive an email via API (for production where SMTP may not be available)"""
    inbox = inbox_name.lower()

    email_id = str(uuid.uuid4())
    email_data = {
        "id": email_id,
        "from_address": from_address,
        "to_address": f"{inbox}@{DOMAIN}",
        "subject": subject,
        "body": body,
        "html_body": html_body,
        "received_at": datetime.now(),
        "forwarded": False,
    }

    if inbox not in emails:
        emails[inbox] = []
    emails[inbox].insert(0, email_data)

    return {"status": "received", "email_id": email_id, "inbox": inbox}


@app.get("/health")
async def health_check():
    """Health check endpoint for Railway"""
    return {"status": "healthy", "service": "disposable-email"}


@app.post("/api/webhooks/resend")
async def resend_webhook(request: Request):
    """Receive inbound emails from Resend"""
    try:
        payload = await request.json()

        # Verify webhook secret if configured
        # Note: Resend doesn't have built-in webhook signature verification yet,
        # but you can add a simple secret check in the URL or header

        # Extract email data from Resend payload
        # Resend inbound webhook format:
        # {
        #   "from": "sender@example.com",
        #   "to": ["inbox@yourdomain.com"],
        #   "subject": "Email subject",
        #   "text": "Plain text body",
        #   "html": "<html>HTML body</html>",
        #   "attachments": [...]
        # }

        from_address = payload.get("from", "")
        to_addresses = payload.get("to", [])
        subject = payload.get("subject", "")
        body = payload.get("text", "")
        html_body = payload.get("html", None)

        # Extract inbox from first to_address
        inbox = "unknown"
        if to_addresses and len(to_addresses) > 0:
            to_address = to_addresses[0]
            inbox = to_address.split("@")[0].lower() if "@" in to_address else to_address.lower()

        # Create email record
        email_id = str(uuid.uuid4())
        email_data = {
            "id": email_id,
            "from_address": from_address,
            "to_address": to_addresses[0] if to_addresses else "",
            "subject": subject,
            "body": body,
            "html_body": html_body,
            "received_at": datetime.now(),
            "forwarded": False,
            "source": "resend",  # Track that it came via Resend
        }

        # Store email
        if inbox not in emails:
            emails[inbox] = []
        emails[inbox].insert(0, email_data)

        print(f"📧 Received email via Resend for inbox '{inbox}': {subject}")

        return JSONResponse(
            status_code=200,
            content={"status": "received", "email_id": email_id, "inbox": inbox}
        )

    except Exception as e:
        print(f"Error processing Resend webhook: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid webhook payload: {str(e)}")


@app.get("/api/resend/status")
async def resend_status():
    """Check Resend integration status"""
    return {
        "configured": bool(RESEND_API_KEY),
        "domain": RESEND_DOMAIN,
        "webhook_url": f"https://{DOMAIN}/api/webhooks/resend",
        "instructions": "Configure this webhook URL in your Resend dashboard under Inbound Emails"
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
        {% if resend_domain %}
        <p style="margin-top: 10px; color: #48bb78; font-size: 14px;">
            <strong>✓ Resend configured:</strong> Emails to <code>*@{{ resend_domain }}</code> will be received
        </p>
        {% endif %}
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

        <!-- Forward Email Section -->
        {% if not email.forwarded %}
        <div class="card" style="background: #f0fff4; border: 1px solid #9ae6b4;">
            <h3>Forward This Email</h3>

            {% if forward_request and not forward_request.verified %}
                <div class="alert alert-info">
                    <strong>Verification Required</strong><br>
                    A 6-digit verification code has been sent to <strong>{{ forward_request.target_email }}</strong>.<br>
                    <small>Code expires in 15 minutes.</small>
                </div>

                <form action="/api/inbox/{{ inbox }}/email/{{ email.id }}/forward-verify" method="post">
                    <input type="hidden" name="target_email" value="{{ forward_request.target_email }}">
                    <div style="display: flex; gap: 10px; flex-wrap: wrap; align-items: flex-end;">
                        <div style="flex: 1; min-width: 200px;">
                            <label style="display: block; margin-bottom: 5px; font-weight: 600;">Verification Code</label>
                            <input type="text" name="verification_code" placeholder="123456" maxlength="6" pattern="[0-9]{6}" required style="margin-bottom: 0;">
                        </div>
                        <button type="submit" class="btn btn-secondary">Verify & Forward</button>
                    </div>
                </form>

            {% else %}
                <p>Send this email to your personal inbox:</p>
                <form action="/api/inbox/{{ inbox }}/email/{{ email.id }}/forward-request" method="post" class="forward-form">
                    <input type="email" name="target_email" placeholder="your-email@example.com" required>
                    <button type="submit" class="btn btn-secondary">Send Verification Code</button>
                </form>
                <p style="margin-top: 10px; font-size: 14px; color: #718096;">
                    You'll receive a 6-digit verification code. Forwarding only happens after verification.
                </p>
            {% endif %}
        </div>
        {% else %}
        <div class="alert alert-success">
            <strong>This email has been forwarded.</strong>
        </div>
        {% endif %}

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

    # Determine public domain for display
    public_domain = RAILWAY_STATIC_URL.replace("https://", "").replace("http://", "") if RAILWAY_STATIC_URL else DOMAIN

    # Start SMTP server (if not disabled)
    smtp_controller = None
    if not IS_PRODUCTION:
        handler = EmailHandler()
        smtp_controller = Controller(handler, hostname=SMTP_HOST, port=SMTP_PORT)
        smtp_controller.start()
        print(f"\nSMTP Server running on {SMTP_HOST}:{SMTP_PORT}")
        print(f"Send emails to: anything@{public_domain}")
    else:
        print("\n⚠️  SMTP Server disabled in production mode")
        print("   Emails can only be received via API in production")

    # Start HTTP server
    import uvicorn

    print(f"\nWeb UI: http://0.0.0.0:{WEB_PORT}")
    if public_domain and public_domain != "localhost":
        print(f"Public URL: https://{public_domain}")
    print(f"Example inbox: http://localhost:{WEB_PORT}/inbox/test")
    print("\nPress Ctrl+C to stop\n")

    config = uvicorn.Config(app, host="0.0.0.0", port=WEB_PORT, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

    # Cleanup
    if smtp_controller:
        smtp_controller.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutting down...")
