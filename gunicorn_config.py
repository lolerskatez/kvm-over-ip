# Gunicorn WSGI application server configuration
# For use with: gunicorn -c gunicorn_config.py app:app

import multiprocessing
import os
from pathlib import Path

# Determine if running in /etc/kvm or locally
ETC_KVM = Path('/etc/kvm').exists()

# Server socket
bind = '127.0.0.1:5000'  # Listen on localhost only (Nginx proxies)
backlog = 2048

# Worker processes
workers = max(2, multiprocessing.cpu_count() - 1)
worker_class = 'sync'  # Use sync for simplicity, or 'gevent' for async
worker_connections = 1000
timeout = 120
graceful_timeout = 30
keepalive = 5

# Process naming
proc_name = 'kvm-over-ip'

# Logging
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
pidfile = '/var/run/kvm-over-ip.pid' if ETC_KVM else None
umask = 0o022
user = 'kvm' if ETC_KVM else None
group = 'kvm' if ETC_KVM else None

# Security
limit_request_line = 4094
limit_request_fields = 100
limit_request_field_size = 8190

# Preload application to reduce startup time per worker
preload_app = True
