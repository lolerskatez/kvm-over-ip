import json
import logging
import smtplib
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

logger = logging.getLogger(__name__)


class NotificationManager:
    """
    Sends notifications via email (SMTP) and webhooks on configurable events.

    Supported events:
        - failed_login
        - session_takeover
        - device_disconnect
        - temperature_alert
        - recording_started

    All sends are non-blocking (threaded) to avoid slowing down request handling.
    """

    def __init__(self, config_loader):
        """
        Initialize notification manager.

        Args:
            config_loader: Callable that returns the current config dict.
        """
        self._load_config = config_loader

    def _get_notif_config(self):
        config = self._load_config()
        return config.get('notifications', {})

    def notify(self, event, message, details=None):
        """
        Send a notification for the given event if enabled.

        Args:
            event: Event name (e.g. 'failed_login').
            message: Human-readable summary string.
            details: Optional dict with extra context.
        """
        nc = self._get_notif_config()
        events = nc.get('events', {})
        if not events.get(event, False):
            return

        payload = {
            'event': event,
            'message': message,
            'details': details or {},
            'timestamp': datetime.utcnow().isoformat(),
        }

        # Email
        email_cfg = nc.get('email', {})
        if email_cfg.get('enabled') and email_cfg.get('to_addrs'):
            threading.Thread(
                target=self._send_email,
                args=(email_cfg, event, message, payload),
                daemon=True,
            ).start()

        # Webhook
        webhook_cfg = nc.get('webhook', {})
        if webhook_cfg.get('enabled') and webhook_cfg.get('url'):
            threading.Thread(
                target=self._send_webhook,
                args=(webhook_cfg, payload),
                daemon=True,
            ).start()

    def _send_email(self, email_cfg, event, message, payload):
        """Send an email notification via SMTP."""
        try:
            host = email_cfg['smtp_host']
            port = int(email_cfg.get('smtp_port', 587))
            user = email_cfg.get('smtp_user', '')
            password = email_cfg.get('smtp_pass', '')
            use_tls = email_cfg.get('smtp_tls', True)
            from_addr = email_cfg.get('from_addr', user)
            to_addrs = email_cfg['to_addrs']

            if not host or not to_addrs:
                return

            subject = f'[KVM-over-IP] {event}: {message}'

            body = f"Event: {event}\n"
            body += f"Message: {message}\n"
            body += f"Time: {payload['timestamp']}\n"
            if payload.get('details'):
                body += f"\nDetails:\n"
                for k, v in payload['details'].items():
                    body += f"  {k}: {v}\n"

            msg = MIMEMultipart()
            msg['From'] = from_addr
            msg['To'] = ', '.join(to_addrs) if isinstance(to_addrs, list) else to_addrs
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain'))

            if use_tls:
                server = smtplib.SMTP(host, port, timeout=15)
                server.starttls()
            else:
                server = smtplib.SMTP(host, port, timeout=15)

            if user and password:
                server.login(user, password)

            server.sendmail(from_addr, to_addrs, msg.as_string())
            server.quit()
            logger.info(f"Email notification sent for event '{event}'")

        except Exception as e:
            logger.error(f"Email notification failed: {e}")

    def _send_webhook(self, webhook_cfg, payload):
        """Send a webhook notification via HTTP POST."""
        try:
            import urllib.request

            url = webhook_cfg['url']
            headers = webhook_cfg.get('headers', {})
            headers.setdefault('Content-Type', 'application/json')

            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(url, data=data, headers=headers, method='POST')
            with urllib.request.urlopen(req, timeout=15) as resp:
                logger.info(f"Webhook notification sent: {resp.status}")

        except Exception as e:
            logger.error(f"Webhook notification failed: {e}")

    def test_email(self, email_cfg):
        """Send a test email to verify SMTP settings.
        Returns True on success, error string on failure."""
        try:
            host = email_cfg.get('smtp_host', '')
            port = int(email_cfg.get('smtp_port', 587))
            user = email_cfg.get('smtp_user', '')
            password = email_cfg.get('smtp_pass', '')
            use_tls = email_cfg.get('smtp_tls', True)
            from_addr = email_cfg.get('from_addr', user)
            to_addrs = email_cfg.get('to_addrs', [])

            if not host or not to_addrs:
                return 'Missing SMTP host or recipients'

            msg = MIMEText('This is a test notification from KVM-over-IP.')
            msg['From'] = from_addr
            msg['To'] = ', '.join(to_addrs) if isinstance(to_addrs, list) else to_addrs
            msg['Subject'] = '[KVM-over-IP] Test Notification'

            if use_tls:
                server = smtplib.SMTP(host, port, timeout=15)
                server.starttls()
            else:
                server = smtplib.SMTP(host, port, timeout=15)

            if user and password:
                server.login(user, password)

            server.sendmail(from_addr, to_addrs, msg.as_string())
            server.quit()
            return True

        except Exception as e:
            return str(e)

    def test_webhook(self, webhook_cfg):
        """Send a test webhook to verify the URL.
        Returns True on success, error string on failure."""
        try:
            import urllib.request

            url = webhook_cfg.get('url', '')
            if not url:
                return 'Missing webhook URL'

            headers = webhook_cfg.get('headers', {})
            headers.setdefault('Content-Type', 'application/json')

            payload = {
                'event': 'test',
                'message': 'Test notification from KVM-over-IP',
                'timestamp': datetime.utcnow().isoformat(),
            }
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(url, data=data, headers=headers, method='POST')
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status < 300:
                    return True
                return f'HTTP {resp.status}'

        except Exception as e:
            return str(e)
