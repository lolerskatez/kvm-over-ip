"""Authentication routes blueprint."""

import os
import secrets
import logging
from flask import (
    Blueprint, render_template, request, jsonify, redirect, url_for, session
)
from flask_login import login_required, login_user, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import pyotp

from app.utils import (
    get_client_ip,
    require_admin,
    load_users,
    save_users,
    load_config,
)

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__)

# Import services from new locations
from app.services.auth.session_manager import SessionManager
from app.services.auth.totp_manager import TOTPManager
from app.services.auth.oidc_auth import OIDCAuth
from app.services.audit.audit_log import AuditLog
from app.services.management.notification_manager import NotificationManager
from app.utils.config import get_config_paths

# Initialize services
paths = get_config_paths()
session_manager = SessionManager(paths['sessions'])
totp_manager = TOTPManager(paths['totp'])
audit_log = AuditLog()

# Will be initialized later in app factory
oidc_auth = None
notification_manager = None


class User:
    """User model for Flask-Login (imported from app factory)."""
    pass


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Login page and authentication."""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard_page'))
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        client_ip = get_client_ip()
        
        if not username or not password:
            return render_template('login.html', error='Username and password required'), 400
        
        if session_manager.is_brute_force_attempt(username):
            logger.warning(f"Brute force attempt detected for user {username} from {client_ip}")
            return render_template('login.html', error='Too many failed attempts. Please try again later.'), 429
        
        # Local authentication (OIDC is handled via /auth/oidc/callback)
        users = load_users()
        user_data = users.get(username)
        if not user_data or not check_password_hash(user_data['password'], password):
            session_manager.record_login_attempt(username, False, client_ip)
            audit_log.log_login(username, client_ip, success=False, failure_reason='invalid_credentials')
            if notification_manager:
                notification_manager.notify('failed_login', f'Failed login for {username}',
                                            {'ip': client_ip, 'method': 'local'})
            logger.warning(f"Failed login attempt for user {username} from {client_ip}")
            return render_template('login.html', error='Invalid credentials'), 401

        # Check if password change is required (new installs or first-time users)
        if user_data.get('requires_password_change', False):
            session['pending_password_change_username'] = username
            session['pending_password_change_ip'] = client_ip
            # Don't log in yet - require password change first
            session.permanent = True
            return redirect(url_for('auth.change_password'))
        
        if totp_manager.is_2fa_enabled(username):
            session['pre_2fa_username'] = username
            session['pre_2fa_ip'] = client_ip
            return redirect(url_for('auth.verify_2fa'))
        
        # Import User from app factory
        from app import User
        user = User(username)
        login_user(user, remember=False)
        session.permanent = True
        # Viewers are read-only; allow concurrent viewer sessions without kicking others out
        user_role = users.get(username, {}).get('role', 'admin' if users.get(username, {}).get('is_admin') else 'operator')
        if user_role == 'viewer':
            session_manager.update_activity(username)
        else:
            session_manager.create_session(username, id(session), client_ip, request.headers.get('User-Agent', ''))
        session_manager.record_login_attempt(username, True, client_ip)
        audit_log.log_login(username, client_ip, success=True)
        logger.info(f"User {username} logged in from {client_ip}")
        return redirect(url_for('dashboard_page'))
    
    return render_template('login.html')


@auth_bp.route('/verify-2fa', methods=['GET', 'POST'])
def verify_2fa():
    """2FA verification page."""
    username = session.get('pre_2fa_username')
    if not username:
        return redirect(url_for('auth.login'))
    
    if request.method == 'POST':
        token = request.form.get('token', '').strip()
        backup_code = request.form.get('backup_code', '').strip()
        client_ip = session.get('pre_2fa_ip', get_client_ip())
        
        verified = False
        if token:
            verified = totp_manager.verify_token(username, token)
        elif backup_code:
            verified = totp_manager.verify_backup_code(username, backup_code)
        
        if verified:
            from app import User
            user = User(username)
            login_user(user, remember=False)
            session.permanent = True
            users = load_users()
            user_role = users.get(username, {}).get('role', 'admin' if users.get(username, {}).get('is_admin') else 'operator')
            if user_role == 'viewer':
                session_manager.update_activity(username)
            else:
                session_manager.create_session(username, id(session), client_ip, request.headers.get('User-Agent', ''))
            session_manager.record_login_attempt(username, True, client_ip)
            session.pop('pre_2fa_username', None)
            session.pop('pre_2fa_ip', None)
            logger.info(f"User {username} passed 2FA from {client_ip}")
            return redirect(url_for('dashboard_page'))
        
        error = 'Invalid 2FA code' if token else 'Invalid backup code' if backup_code else 'Please enter a code'
        return render_template('verify_2fa.html', error=error), 401
    
    return render_template('verify_2fa.html')


@auth_bp.route('/change-password', methods=['GET', 'POST'])
def change_password():
    """Force password change on first login (no @login_required - handle auth manually)."""
    # Check if user is in pending password change state
    username = session.get('pending_password_change_username')
    
    # If not in pending state, check if already logged in and password change already done
    if not username:
        if current_user.is_authenticated:
            return redirect(url_for('dashboard_page'))
        return redirect(url_for('auth.login'))
    
    users = load_users()
    user_data = users.get(username, {})
    
    # Double-check the flag is still set (shouldn't change during this request)
    if not user_data.get('requires_password_change', False):
        # Password change already completed, now we can log them in
        from app import User
        user = User(username)
        login_user(user, remember=False)
        session.permanent = True
        session.pop('pending_password_change_username', None)
        session.pop('pending_password_change_ip', None)
        return redirect(url_for('dashboard_page'))
    
    if request.method == 'POST':
        old_password = request.form.get('old_password', '')
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')
        
        # Validate old password
        if not check_password_hash(user_data['password'], old_password):
            return render_template('change_password.html', 
                                 error='Current password is incorrect', 
                                 username=username), 401
        
        # Validate new password
        if not new_password or len(new_password) < 8:
            return render_template('change_password.html',
                                 error='Password must be at least 8 characters',
                                 username=username), 400
        
        if new_password != confirm_password:
            return render_template('change_password.html',
                                 error='Passwords do not match',
                                 username=username), 400
        
        # Update password and clear the flag
        user_data['password'] = generate_password_hash(new_password)
        user_data['requires_password_change'] = False
        users[username] = user_data
        
        if save_users(users):
            session.pop('pending_password_change_username', None)
            session.pop('pending_password_change_ip', None)
            
            # Now log the user in
            from app import User
            user = User(username)
            login_user(user, remember=False)
            session.permanent = True
            
            # Check if 2FA is enabled for this user
            if totp_manager.is_2fa_enabled(username):
                session['pre_2fa_username'] = username
                session['pre_2fa_ip'] = get_client_ip()
                return redirect(url_for('auth.verify_2fa'))
            
            # Proceed to dashboard
            logger.info(f"User {username} completed forced password change")
            client_ip = session.get('pending_password_change_ip', get_client_ip())
            user_role = user_data.get('role', 'admin' if user_data.get('is_admin') else 'operator')
            if user_role != 'viewer':
                session_manager.create_session(username, id(session), client_ip, request.headers.get('User-Agent', ''))
            return redirect(url_for('dashboard_page'))
        else:
            return render_template('change_password.html',
                                 error='Failed to save new password',
                                 username=username), 500
    
    return render_template('change_password.html', username=username)


@auth_bp.route('/logout')
@login_required
def logout():
    """Logout user."""
    username = current_user.username
    client_ip = get_client_ip()
    session_manager.end_session(username)
    audit_log.log_logout(username, client_ip, reason='user_logout')
    logout_user()
    logger.info(f"User {username} logged out")
    return redirect(url_for('auth.login'))


@auth_bp.route('/auth/oidc/login')
def oidc_login():
    """Redirect to the OIDC provider's authorization endpoint."""
    if not oidc_auth or not oidc_auth.is_enabled:
        return redirect(url_for('auth.login'))
    state = secrets.token_urlsafe(32)
    session['oidc_state'] = state
    redirect_uri = url_for('auth.oidc_callback', _external=True)
    try:
        auth_url = oidc_auth.get_authorization_url(redirect_uri, state)
    except Exception as e:
        logger.error(f"OIDC authorization URL error: {e}")
        return render_template('login.html', error='SSO configuration error'), 500
    return redirect(auth_url)


@auth_bp.route('/auth/oidc/callback')
def oidc_callback():
    """Handle the OIDC provider's authorization code callback."""
    if not oidc_auth:
        return redirect(url_for('auth.login'))
    
    # Validate state to prevent CSRF
    state = request.args.get('state', '')
    expected = session.pop('oidc_state', None)
    if not expected or not secrets.compare_digest(state, expected):
        logger.warning("OIDC callback received invalid state")
        return render_template('login.html', error='SSO login failed (invalid state)'), 400

    error = request.args.get('error')
    if error:
        desc = request.args.get('error_description', error)
        logger.warning(f"OIDC provider returned error: {desc}")
        return render_template('login.html', error=f'SSO login failed: {desc}'), 401

    code = request.args.get('code', '')
    if not code:
        return render_template('login.html', error='SSO login failed (no code)'), 400

    client_ip = get_client_ip()
    try:
        redirect_uri = url_for('auth.oidc_callback', _external=True)
        userinfo = oidc_auth.exchange_code(redirect_uri, code)
    except Exception as e:
        logger.error(f"OIDC code exchange failed: {e}")
        return render_template('login.html', error='SSO login failed (token exchange error)'), 401

    username = oidc_auth.get_username(userinfo)
    if not username:
        logger.warning("OIDC userinfo missing username claim")
        return render_template('login.html', error='SSO login failed (no username in token)'), 401

    if not oidc_auth.is_allowed(userinfo):
        logger.warning(f"OIDC login denied for {username}: not in an allowed group")
        audit_log.log_login(username, client_ip, success=False, failure_reason='oidc_group_not_allowed')
        return render_template('login.html', error='SSO login denied: your account is not in an authorised group.'), 403

    is_admin = oidc_auth.is_admin(userinfo)

    # Auto-provision or update local user entry
    users = load_users()
    if username not in users:
        users[username] = {
            'password': generate_password_hash(secrets.token_urlsafe(32)),
            'is_admin': is_admin,
            'oidc': True,
        }
        save_users(users)
        logger.info(f"Auto-provisioned OIDC user: {username}")
    elif users[username].get('oidc'):
        # Refresh admin status on each OIDC login
        users[username]['is_admin'] = is_admin
        save_users(users)

    if totp_manager.is_2fa_enabled(username):
        session['pre_2fa_username'] = username
        session['pre_2fa_ip'] = client_ip
        return redirect(url_for('auth.verify_2fa'))

    from app import User
    user = User(username)
    login_user(user, remember=False)
    session.permanent = True
    user_role = users.get(username, {}).get('role', 'admin' if is_admin else 'operator')
    if user_role == 'viewer':
        session_manager.update_activity(username)
    else:
        session_manager.create_session(username, id(session), client_ip, request.headers.get('User-Agent', ''))
    session_manager.record_login_attempt(username, True, client_ip)
    audit_log.log_login(username, client_ip, success=True)
    logger.info(f"OIDC user {username} logged in from {client_ip}")
    return redirect(url_for('dashboard_page'))


# 2FA Management API endpoints
@auth_bp.route('/api/2fa/setup', methods=['POST'])
@login_required
def api_2fa_setup():
    """Generate 2FA secret and QR code."""
    username = current_user.username
    secret, uri = totp_manager.generate_secret(username)
    qr_code = totp_manager.get_qr_code(username, secret)
    session['pending_2fa_secret'] = secret
    return jsonify({'qr_code': qr_code, 'secret': secret, 'uri': uri})


@auth_bp.route('/api/2fa/confirm', methods=['POST'])
@login_required
def api_2fa_confirm():
    """Confirm and enable 2FA with a valid token."""
    data = request.get_json()
    token = data.get('token', '').strip()
    secret = session.get('pending_2fa_secret')
    
    if not secret:
        return jsonify({'error': 'No 2FA setup in progress'}), 400
    
    totp = pyotp.TOTP(secret)
    if not totp.verify(token, valid_window=1):
        return jsonify({'error': 'Invalid token'}), 401
    
    username = current_user.username
    if totp_manager.enable_2fa(username, secret):
        session.pop('pending_2fa_secret', None)
        backup_codes = totp_manager.get_backup_codes(username)
        logger.info(f"2FA enabled for user {username}")
        return jsonify({'status': 'ok', 'backup_codes': backup_codes})
    return jsonify({'error': 'Failed to enable 2FA'}), 500


@auth_bp.route('/api/2fa/disable', methods=['POST'])
@login_required
def api_2fa_disable():
    """Disable 2FA for current user."""
    data = request.get_json()
    password = data.get('password', '')
    users = load_users()
    user_data = users.get(current_user.username)
    
    if not user_data or not check_password_hash(user_data['password'], password):
        return jsonify({'error': 'Invalid password'}), 401
    
    if totp_manager.disable_2fa(current_user.username):
        logger.info(f"2FA disabled for user {current_user.username}")
        return jsonify({'status': 'ok'})
    return jsonify({'error': 'Failed to disable 2FA'}), 500


@auth_bp.route('/api/2fa/status', methods=['GET'])
@login_required
def api_2fa_status():
    """Get 2FA status for current user."""
    return jsonify(totp_manager.get_user_2fa_status(current_user.username))


@auth_bp.route('/api/change-password', methods=['POST'])
@login_required
def api_change_password():
    """Change current user's password."""
    data = request.get_json()
    current_password = data.get('current_password', '')
    new_password = data.get('new_password', '')
    
    if not current_password or not new_password:
        return jsonify({'error': 'Both passwords required'}), 400
    
    if len(new_password) < 8:
        return jsonify({'error': 'New password must be at least 8 characters'}), 400
    
    users = load_users()
    user_data = users.get(current_user.username)
    
    if not user_data or not check_password_hash(user_data['password'], current_password):
        return jsonify({'error': 'Current password is incorrect'}), 401
    
    users[current_user.username]['password'] = generate_password_hash(new_password)
    
    if save_users(users):
        logger.info(f"Password changed for user {current_user.username}")
        return jsonify({'status': 'ok'})
    else:
        return jsonify({'error': 'Failed to change password'}), 500
