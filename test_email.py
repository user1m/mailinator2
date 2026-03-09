#!/usr/bin/env python3
"""
Test script for the Disposable Email Service
Sends a test email to the local SMTP server
"""

import smtplib
import sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def send_test_email(inbox_name="test", subject="Test Email", body="This is a test email!"):
    """Send a test email to the local SMTP server"""

    # SMTP server settings
    smtp_host = "localhost"
    smtp_port = 2525

    # Create message
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = "sender@example.com"
    msg["To"] = f"{inbox_name}@localhost"

    # Create both plain text and HTML versions
    text_part = MIMEText(body, "plain")
    html_part = MIMEText(f"""
    <html>
        <body>
            <h2>{subject}</h2>
            <p>{body}</p>
            <p><em>Sent via Disposable Email Service Test Script</em></p>
        </body>
    </html>
    """, "html")

    msg.attach(text_part)
    msg.attach(html_part)

    try:
        # Connect to SMTP server
        print(f"Connecting to SMTP server at {smtp_host}:{smtp_port}...")
        smtp = smtplib.SMTP(smtp_host, smtp_port)

        # Send email
        print(f"Sending email to {inbox_name}@localhost...")
        smtp.sendmail("sender@example.com", f"{inbox_name}@localhost", msg.as_string())

        # Close connection
        smtp.quit()

        print(f"✓ Email sent successfully!")
        print(f"  To: {inbox_name}@localhost")
        print(f"  Subject: {subject}")
        print(f"\nView inbox at: http://localhost:8000/inbox/{inbox_name}")

        return True

    except ConnectionRefusedError:
        print("✗ Error: Could not connect to SMTP server.")
        print("  Make sure the service is running: python main.py")
        return False
    except Exception as e:
        print(f"✗ Error sending email: {e}")
        return False


if __name__ == "__main__":
    # Get inbox name from command line or use default
    inbox = sys.argv[1] if len(sys.argv) > 1 else "test"
    subject = sys.argv[2] if len(sys.argv) > 2 else "Test Email"
    body = sys.argv[3] if len(sys.argv) > 3 else "This is a test message from the disposable email service!"

    send_test_email(inbox, subject, body)