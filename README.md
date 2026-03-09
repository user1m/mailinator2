# Disposable Email Service with Forwarding

A Mailinator-like disposable email service built with Python and FastAPI, featuring the ability to forward emails from temporary inboxes to your real email address.

## Features

- **Instant Inboxes** - Create temporary email addresses on the fly
- **Email Forwarding** - Forward important emails from disposable inboxes to your real email address
- **Verification Required** - Email verification prevents spam abuse
- **Auto-Cleanup** - Emails automatically deleted after 24 hours
- **Web Interface** - Clean, responsive UI for viewing emails
- **JSON API** - RESTful API for programmatic access

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Run the Service

```bash
python main.py
```

This starts:
- **SMTP Server** on port 2525 (receives incoming emails)
- **Web UI/API** on port 8000 (view inboxes, configure forwarding)

### 3. Test It

Send a test email via SMTP:
```bash
# Using Python
python -c "
import smtplib
smtp = smtplib.SMTP('localhost', 2525)
smtp.sendmail('sender@example.com', 'test@localhost', 'Subject: Hello\\n\\nTest message!')
smtp.quit()
"
```

Or view the web interface:
```
http://localhost:8000/inbox/test
```

## How Email Forwarding Works

1. **Create an inbox** - Visit any inbox URL (e.g., `/inbox/my-temp-inbox`)
2. **Set up forwarding** - Enter your real email address in the forwarding form
3. **Verify your email** - Click the verification link (shown in console for demo, sent via email in production)
4. **Start receiving** - All emails to your disposable inbox are now forwarded to your real address

### Verification Flow

```
┌─────────────┐      ┌─────────────┐      ┌─────────────┐
│   User      │─────▶│  Web Form   │─────▶│  Generate   │
│             │      │  (inbox)    │      │   Token     │
└─────────────┘      └─────────────┘      └──────┬──────┘
                                                 │
                                                 ▼
                                          ┌─────────────┐
                                          │  Send       │
                                          │ Verification│
                                          │   Email     │
                                          └──────┬──────┘
                                                 │
                                                 ▼
┌─────────────┐      ┌─────────────┐      ┌─────────────┐
│ Forwarding  │◀─────│  Verify     │◀─────│ User Clicks │
│   Active    │      │   Token     │      │    Link     │
└─────────────┘      └─────────────┘      └─────────────┘
```

## Configuration

Set environment variables to customize:

| Variable | Default | Description |
|----------|---------|-------------|
| `SMTP_HOST` | `0.0.0.0` | SMTP server bind address |
| `SMTP_PORT` | `2525` | SMTP server port |
| `WEB_PORT` | `8000` | Web UI/API port |
| `DOMAIN` | `localhost` | Domain for email addresses |
| `MAX_EMAIL_AGE_HOURS` | `24` | How long to keep emails |
| `FORWARD_VERIFICATION_EXPIRY_HOURS` | `24` | Verification link expiry |

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Home page |
| GET | `/inbox/{name}` | View inbox (HTML) |
| GET | `/inbox/{name}/email/{id}` | View single email (HTML) |
| POST | `/api/inbox/{name}/forward` | Set up forwarding |
| POST | `/api/inbox/{name}/forward/disable` | Disable forwarding |
| DELETE | `/api/inbox/{name}/forward` | Remove forwarding rule |
| GET | `/verify/{inbox}/{token}` | Verify forwarding email |
| GET | `/api/inbox/{name}/emails` | Get emails (JSON) |
| GET | `/api/stats` | Service statistics |

## Enabling Real Email Forwarding

The demo implementation logs forwarding actions to the console. To enable real email delivery:

1. Uncomment the `aiosmtplib.send()` code in `main.py`
2. Configure your SMTP credentials:
   ```python
   await aiosmtplib.send(
       msg,
       hostname="smtp.gmail.com",
       port=587,
       username="your-email@gmail.com",
       password="your-app-password",
       start_tls=True,
   )
   ```

3. Or better, use environment variables and a proper email service (SendGrid, AWS SES, etc.)

## Production Considerations

- **Storage**: Replace in-memory storage with Redis or PostgreSQL
- **Rate Limiting**: Add rate limits to prevent abuse
- **Authentication**: Consider adding simple auth for forwarding setup
- **Email Delivery**: Use a transactional email service for reliability
- **Spam Filtering**: Add basic spam detection before forwarding
- **HTTPS**: Always use HTTPS in production

## Architecture

```
                    ┌─────────────────┐
                    │  External SMTP  │
                    │   (sends mail)  │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │  aiosmtpd       │
                    │  (SMTP server)  │
                    │   Port 2525     │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │  EmailHandler   │
                    │  - Parse email  │
                    │  - Store in mem │
                    │  - Forward?     │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
              ▼              ▼              ▼
        ┌─────────┐   ┌──────────┐  ┌──────────┐
        │  Store  │   │   Web    │  │ Forward  │
        │  Email  │   │   UI     │  │  (SMTP)  │
        └─────────┘   │ Port 8000│  └──────────┘
                      └──────────┘
```

## License

MIT