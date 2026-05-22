"""Security decorators and access control functions."""

import logging
import secrets
import ipaddress
from functools import wraps
from flask import session, jsonify, request, redirect, url_for
from flask_login import current_user

logger = logging.getLogger(__name__)

from .config import load_users


def require_admin(f):
    """Decorator to require admin access."""
    @wraps(f)
    def decorated(*args, **kwargs):
        users = load_users()
        if not users.get(current_user.username, {}).get('is_admin', False):
            return jsonify({'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated


def require_operator(f):
    """Decorator to require operator or admin access (blocks viewer role)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        users = load_users()
        user_data = users.get(current_user.username, {})
        role = user_data.get('role', 'admin' if user_data.get('is_admin') else 'operator')
        if role == 'viewer':
            return jsonify({'error': 'This action requires operator or admin access'}), 403
        return f(*args, **kwargs)
    return decorated


# Alias for HID endpoints — operator/admin can control, viewer cannot
require_kvm_access = require_operator


def generate_csrf_token():
    """Generate and store a CSRF token in the session."""
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_hex(32)
    return session['_csrf_token']


def validate_csrf_token():
    """Validate CSRF token from request."""
    token = request.headers.get('X-CSRF-Token') or request.form.get('_csrf_token')
    if not token or token != session.get('_csrf_token'):
        return False
    return True


def get_client_ip():
    """Get client IP address."""
    return request.headers.get('X-Forwarded-For', request.remote_addr or '0.0.0.0')


def check_ip_acl(client_ip):
    """Check if a client IP is allowed by the IP ACL rules.
    Returns True if allowed, False if blocked."""
    from .config import load_config
    
    config = load_config()
    acl = config.get('ip_acl', {})
    if not acl.get('enabled', False):
        return True

    mode = acl.get('mode', 'whitelist')
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False

    if mode == 'whitelist':
        entries = acl.get('whitelist', [])
        if not entries:
            return True
        for entry in entries:
            try:
                if '/' in entry:
                    if addr in ipaddress.ip_network(entry, strict=False):
                        return True
                else:
                    if addr == ipaddress.ip_address(entry):
                        return True
            except ValueError:
                continue
        return False

    elif mode == 'blacklist':
        entries = acl.get('blacklist', [])
        for entry in entries:
            try:
                if '/' in entry:
                    if addr in ipaddress.ip_network(entry, strict=False):
                        return False
                else:
                    if addr == ipaddress.ip_address(entry):
                        return False
            except ValueError:
                continue
        return True

    return True
