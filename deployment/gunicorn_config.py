# Gunicorn WSGI application server configuration
# For use with: gunicorn -c gunicorn_config.py "app:create_app()"

import multiprocessing
import os
from pathlib import Path

# Determine if running in /etc/kvm or locally
ETC_KVM = Path('/etc/kvm').exists()

# Server socket
# For Docker: bind to all interfaces. For system install with Nginx: bind to localhost.
if os.getenv('CONTAINER'):
    bind = '0.0.0.0:8000'  # Docker: listen on all interfaces
else:
    bind = '127.0.0.1:5000'  # System: listen on localhost only (Nginx proxies)
backlog = 2048

# Worker processes
# Use only 1 worker in containers to avoid hardware conflicts (video/HID devices)
if os.getenv('CONTAINER'):
    workers = 1
else:
    workers = max(2, multiprocessing.cpu_count() - 1)
worker_class = 'sync'  # Use sync for simplicity, or 'gevent' for async
worker_connections = 1000
timeout = 120
graceful_timeout = 30
keepalive = 5

# Process naming
proc_name = 'kvm-over-ip'

# Logging
# For Docker: log to stdout/stderr. For system install: log to files.
if os.getenv('CONTAINER'):
    accesslog = '-'  # stdout
    errorlog = '-'   # stderr
else:
    accesslog = '/var/log/kvm-over-ip/gunicorn_access.log' if ETC_KVM else './logs/gunicorn_access.log'
    errorlog = '/var/log/kvm-over-ip/gunicorn_error.log' if ETC_KVM else './logs/gunicorn_error.log'
loglevel = 'info'
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# SSL/TLS (HTTPS is recommended for production)
# For testing: SSL is commented out below. Uncomment to enable HTTPS.
# Note: Certificates are required. Use cert_manager.py to generate them:
#   python3 cert_manager.py --self-signed
# Then uncomment the lines below:
# keyfile = '/etc/kvm/key.pem' if ETC_KVM else './key.pem'
# certfile = '/etc/kvm/cert.pem' if ETC_KVM else './cert.pem'
# ssl_version = 'TLSv1_2'

# Server mechanics
daemon = False
# Don't use pidfile in containers (permission issues)
pidfile = '/var/run/kvm-over-ip.pid' if (ETC_KVM and not os.getenv('CONTAINER')) else None
umask = 0o022
# Don't set user/group when running in Docker
user = 'kvm' if (ETC_KVM and not os.getenv('CONTAINER')) else None
group = 'kvm' if (ETC_KVM and not os.getenv('CONTAINER')) else None

# Security
limit_request_line = 4094
limit_request_fields = 100
limit_request_field_size = 8190

# Preload application to reduce startup time per worker
preload_app = True
