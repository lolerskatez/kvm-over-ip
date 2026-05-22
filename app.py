import os
import re
import json
import signal
import secrets
import logging
import ipaddress
import threading
from functools import wraps
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, render_template, request, jsonify, redirect, url_for, session, Response, send_file
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_sock import Sock
from werkzeug.security import generate_password_hash, check_password_hash

import pyotp

from hid_controller import CH9329HIDController
from video_streamer import VideoStreamer
from session_manager import SessionManager
from totp_manager import TOTPManager
from pxe_server import PXEServer
from system_monitor import SystemMonitor
from session_recorder import SessionRecorder
from cert_manager import CertManager
from edid_manager import EDIDManager
from macro_manager import MacroManager
from notification_manager import NotificationManager
from oidc_auth import OIDCAuth
from backup_manager import BackupManager
from audit_log import AuditLog
from wake_on_lan import WakeOnLANManager
from config_crypto import ConfigCrypto

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
sock = Sock(app)

def get_or_generate_secret_key():
    """Get SECRET_KEY from env or generate and persist a random one.
    
    IMPORTANT: For production/Gunicorn deployments, set SECRET_KEY environment variable
    to ensure sessions persist across application restarts. If not set, a random key
    is generated and persisted to disk (./secret_key for dev, /etc/kvm/secret_key for prod).
    """
    key = os.environ.get('SECRET_KEY')
    if key:
        return key
    key_file = Path('./secret_key')
    if os.path.exists('/etc/kvm'):
        key_file = Path('/etc/kvm/secret_key')
    if key_file.exists():
        return key_file.read_text().strip()
    key = secrets.token_hex(32)
    try:
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text(key)
        key_file.chmod(0o600)
    except Exception as e:
        logger.warning(f"Could not persist secret key: {e}")
    return key

app.config['SECRET_KEY'] = get_or_generate_secret_key()
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = 28800

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

CONFIG_PATH = Path('/etc/kvm/config.json') if os.path.exists('/etc/kvm') else Path('./config.json')
USERS_PATH = Path('/etc/kvm/users.json') if os.path.exists('/etc/kvm') else Path('./users.json')
SESSIONS_PATH = '/etc/kvm/sessions.json' if os.path.exists('/etc/kvm') else './sessions.json'
TOTP_PATH = '/etc/kvm/totp_secrets.json' if os.path.exists('/etc/kvm') else './totp_secrets.json'

hid_controller = None
video_streamer = None
pxe_server = None
system_monitor = SystemMonitor()
session_recorder = None
cert_manager = None
edid_manager = None
macro_manager = None
notification_manager = None
oidc_auth = None
backup_manager = None
session_manager = SessionManager(SESSIONS_PATH)
totp_manager = TOTPManager(TOTP_PATH)
audit_log = AuditLog()
wol_manager = WakeOnLANManager()

# Config encryption (enabled when config.key exists)
_crypto_key_path = Path('/etc/kvm/config.key') if os.path.exists('/etc/kvm') else Path('./config.key')
config_crypto = ConfigCrypto(key_path=str(_crypto_key_path))


class User(UserMixin):
    """User model for Flask-Login."""
    def __init__(self, username):
        self.id = username
        self.username = username
        users = load_users()
        user_data = users.get(username, {})
        self.is_admin = user_data.get('is_admin', False)
        # role: 'admin' | 'operator' | 'viewer'
        # Derive from explicit role field or fall back to is_admin
        self.role = user_data.get('role', 'admin' if self.is_admin else 'operator')


@login_manager.user_loader
def load_user(username):
    """Load user only if they exist in users.json."""
    users = load_users()
    if username in users:
        return User(username)
    return None


def get_client_ip():
    """Get client IP address."""
    return request.headers.get('X-Forwarded-For', request.remote_addr or '0.0.0.0')


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


def check_ip_acl(client_ip):
    """Check if a client IP is allowed by the IP ACL rules.
    Returns True if allowed, False if blocked."""
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


@app.before_request
def ip_acl_check():
    """Block requests from disallowed IPs."""
    if request.path in ('/health', '/healthz'):
        return
    client_ip = get_client_ip()
    if not check_ip_acl(client_ip):
        logger.warning(f"IP ACL blocked request from {client_ip} to {request.path}")
        return jsonify({'error': 'Access denied'}), 403


@app.before_request
def csrf_protect():
    """Check CSRF token on state-changing requests."""
    if request.method in ('POST', 'PUT', 'DELETE'):
        if request.path in ('/login', '/verify-2fa'):
            return
        if not validate_csrf_token():
            return jsonify({'error': 'CSRF token missing or invalid'}), 403
    # iPXE boot menu and image serving are unauthenticated GET routes —
    # no CSRF exemption needed (GET is already exempt above).


@app.before_request
def check_inactivity():
    """Auto-logout users who exceed the idle timeout."""
    if not current_user.is_authenticated:
        return
    # Skip for static/non-interactive endpoints
    if request.path in ('/logout', '/login', '/health', '/healthz'):
        return

    config = load_config()
    idle_timeout = config.get('idle_timeout', 900)  # default 15 min
    if idle_timeout <= 0:
        # Disabled
        session_manager.update_activity(current_user.username)
        return

    if session_manager.is_idle_timeout(current_user.username, idle_timeout):
        username = current_user.username
        session_manager.end_session(username)
        logout_user()
        logger.info(f"User {username} auto-logged out due to inactivity ({idle_timeout}s)")
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Session expired due to inactivity', 'idle_logout': True}), 401
        return redirect(url_for('login'))

    session_manager.update_activity(current_user.username)


@app.context_processor
def inject_template_globals():
    """Make CSRF token, OIDC state, and user role available to all templates."""
    role = 'viewer'
    if current_user.is_authenticated:
        users = load_users()
        user_data = users.get(current_user.username, {})
        role = user_data.get('role', 'admin' if user_data.get('is_admin') else 'operator')
    return dict(
        csrf_token=generate_csrf_token,
        oidc_enabled=bool(oidc_auth and oidc_auth.is_enabled),
        current_role=role,
    )


def load_config():
    """Load configuration from JSON file."""
    try:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, 'r') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
    
    return {
        'video_device': '/dev/video0',
        'hid_device': '/dev/ttyUSB0',
        'resolution': '1280x720',
        'framerate': 15,
        'bitrate': '2000k'
    }


def save_config(config):
    """Save configuration to JSON file."""
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, 'w') as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Failed to save config: {e}")
        return False


def load_users():
    """Load users from JSON file (or encrypted .enc file)."""
    try:
        data = config_crypto.load(USERS_PATH)
        if data is not None:
            return data
    except Exception as e:
        logger.error(f"Failed to load users: {e}")
    return {}


def save_users(users):
    """Save users to disk (encrypted if config.key exists, plaintext otherwise)."""
    try:
        USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
        return config_crypto.save(USERS_PATH, users)
    except Exception as e:
        logger.error(f"Failed to save users: {e}")
        return False


def ensure_password_change_flag():
    """Migrate existing users to require password change on next login if flag not set.
    
    This ensures that users from previous installations are forced to change
    their password to a new one (especially important for default credentials).
    """
    try:
        users = load_users()
        needs_save = False
        for username, user_data in users.items():
            if 'requires_password_change' not in user_data:
                user_data['requires_password_change'] = True
                needs_save = True
                logger.info(f"Marked user {username} for password change migration")
        if needs_save:
            save_users(users)
            logger.info("Password change flag migration completed")
    except Exception as e:
        logger.error(f"Failed to migrate password change flags: {e}")


def validate_config_value(key, value):
    """Validate configuration values to prevent injection."""
    if key in ('video_device', 'hid_device'):
        if not isinstance(value, str) or not re.match(r'^/dev/[a-zA-Z0-9_/]+$', value):
            return False
    elif key == 'resolution':
        if not isinstance(value, str) or not re.match(r'^\d{3,4}x\d{3,4}$', value):
            return False
    elif key == 'framerate':
        if not isinstance(value, int) or value < 1 or value > 60:
            return False
    elif key == 'bitrate':
        if not isinstance(value, str) or not re.match(r'^\d+k$', value):
            return False
    elif key == 'idle_timeout':
        if not isinstance(value, int) or value < 0 or value > 86400:
            return False
    return True


def init_hid():
    """Initialize HID controller."""
    global hid_controller
    config = load_config()
    hid_device = config.get('hid_device', '/dev/ttyUSB0')
    
    hid_controller = CH9329HIDController(port=hid_device)
    if hid_controller.connect():
        logger.info("HID controller initialized")
        return True
    else:
        logger.warning("HID controller failed to connect (device may not be available)")
        return False


def init_video():
    """Initialize video streamer."""
    global video_streamer
    config = load_config()
    video_device = config.get('video_device', '/dev/video0')
    resolution = config.get('resolution', '1280x720')
    framerate = config.get('framerate', 15)
    bitrate = config.get('bitrate', '2000k')
    
    video_streamer = VideoStreamer(
        video_device=video_device,
        resolution=resolution,
        framerate=framerate,
        bitrate=bitrate
    )
    if video_streamer.start():
        logger.info("Video streamer initialized")
        return True
    else:
        logger.warning("Video streamer failed to start (device may not be available)")
        return False


def init_pxe():
    """Initialize PXE server from config (does not auto-start dnsmasq)."""
    global pxe_server
    config = load_config()
    pxe_config = config.get('pxe', {})

    pxe_server = PXEServer(
        base_dir=pxe_config.get('base_dir', '/var/lib/kvm/pxe'),
        http_port=5000,
    )
    pxe_server.apply_config(pxe_config)
    pxe_server.setup_directories()
    logger.info("PXE server initialized (not started)")


def init_recorder():
    """Initialize session recorder."""
    global session_recorder
    config = load_config()
    rec_config = config.get('recording', {})
    recordings_dir = rec_config.get('recordings_dir', '/var/lib/kvm/recordings')
    max_recordings = rec_config.get('max_recordings', 50)
    session_recorder = SessionRecorder(recordings_dir=recordings_dir, max_recordings=max_recordings)
    session_recorder.setup()
    logger.info("Session recorder initialized")


def init_cert_manager():
    """Initialize certificate manager."""
    global cert_manager
    cert_manager = CertManager(cert_dir='.')
    logger.info("Certificate manager initialized")


def init_edid():
    """Initialize EDID manager."""
    global edid_manager
    config = load_config()
    video_device = config.get('video_device', '/dev/video0')
    edid_manager = EDIDManager(video_device=video_device)
    logger.info("EDID manager initialized")


def setup_remote_syslog():
    """Configure remote syslog forwarding if configured."""
    config = load_config()
    syslog_config = config.get('syslog', {})
    host = syslog_config.get('host', '')
    port = syslog_config.get('port', 514)

    if not host:
        return

    try:
        import logging.handlers
        handler = logging.handlers.SysLogHandler(
            address=(host, int(port)),
            facility=logging.handlers.SysLogHandler.LOG_LOCAL0,
        )
        handler.setLevel(logging.INFO)
        fmt = logging.Formatter('kvm-over-ip: %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(fmt)
        logging.getLogger().addHandler(handler)
        logger.info(f"Remote syslog configured: {host}:{port}")
    except Exception as e:
        logger.warning(f"Failed to configure remote syslog: {e}")


def init_macros():
    """Initialize macro manager."""
    global macro_manager
    macros_path = '/etc/kvm/macros.json' if os.path.exists('/etc/kvm') else './macros.json'
    macro_manager = MacroManager(macros_path=macros_path)
    logger.info("Macro manager initialized")


def init_notifications():
    """Initialize notification manager."""
    global notification_manager
    notification_manager = NotificationManager(config_loader=load_config)
    logger.info("Notification manager initialized")


def init_oidc():
    """Initialize OIDC authentication backend."""
    global oidc_auth
    config = load_config()
    oidc_auth = OIDCAuth(config=config.get('oidc', {}))
    logger.info(f"OIDC auth initialized (available={oidc_auth.is_available}, enabled={oidc_auth.is_enabled})")


def init_backup():
    """Initialize backup manager."""
    global backup_manager
    config_dir = '/etc/kvm' if os.path.exists('/etc/kvm') else '.'
    backup_manager = BackupManager(config_dir=config_dir)
    logger.info("Backup manager initialized")


@app.route('/')
def index():
    """Redirect to login or dashboard."""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard_page'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
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
            return redirect(url_for('change_password'))
        
        if totp_manager.is_2fa_enabled(username):
            session['pre_2fa_username'] = username
            session['pre_2fa_ip'] = client_ip
            return redirect(url_for('verify_2fa'))
        
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


@app.route('/verify-2fa', methods=['GET', 'POST'])
def verify_2fa():
    """2FA verification page."""
    username = session.get('pre_2fa_username')
    if not username:
        return redirect(url_for('login'))
    
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


@app.route('/change-password', methods=['GET', 'POST'])
def change_password():
    """Force password change on first login (no @login_required - handle auth manually)."""
    # Check if user is in pending password change state
    username = session.get('pending_password_change_username')
    
    # If not in pending state, check if already logged in and password change already done
    if not username:
        if current_user.is_authenticated:
            return redirect(url_for('dashboard_page'))
        return redirect(url_for('login'))
    
    users = load_users()
    user_data = users.get(username, {})
    
    # Double-check the flag is still set (shouldn't change during this request)
    if not user_data.get('requires_password_change', False):
        # Password change already completed, now we can log them in
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
            user = User(username)
            login_user(user, remember=False)
            session.permanent = True
            
            # Check if 2FA is enabled for this user
            if totp_manager.is_2fa_enabled(username):
                session['pre_2fa_username'] = username
                session['pre_2fa_ip'] = get_client_ip()
                return redirect(url_for('verify_2fa'))
            
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


@app.route('/logout')
@login_required
def logout():
    """Logout user."""
    username = current_user.username
    client_ip = get_client_ip()
    session_manager.end_session(username)
    audit_log.log_logout(username, client_ip, reason='user_logout')
    logout_user()
    logger.info(f"User {username} logged out")
    return redirect(url_for('login'))


@app.route('/users')
@login_required
def users_page():
    """User management page (admin only)."""
    users = load_users()
    if not users.get(current_user.username, {}).get('is_admin', False):
        return redirect(url_for('console'))
    return render_template('users.html')


@app.route('/settings')
@login_required
def settings_page():
    """Settings page (admin only)."""
    users = load_users()
    if not users.get(current_user.username, {}).get('is_admin', False):
        return redirect(url_for('console'))
    return render_template('settings.html')


@app.route('/wake-on-lan')
@login_required
def wake_on_lan_page():
    """Wake-on-LAN page."""
    return render_template('wake_on_lan.html')


@app.route('/api/users', methods=['GET'])
@login_required
@require_admin
def api_get_users():
    """Get user list (admin only)."""
    users = load_users()
    
    user_list = []
    for username, user_data in users.items():
        totp_status = totp_manager.get_user_2fa_status(username)
        is_admin = user_data.get('is_admin', False)
        role = user_data.get('role', 'admin' if is_admin else 'operator')
        user_list.append({
            'username': username,
            'is_admin': is_admin,
            'role': role,
            '2fa_enabled': totp_status['enabled']
        })
    
    return jsonify({'users': user_list})


@app.route('/api/users', methods=['POST'])
@login_required
@require_admin
def api_create_user():
    """Create new user (admin only)."""
    users = load_users()
    
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '')
    is_admin = data.get('is_admin', False)
    role = data.get('role', 'admin' if is_admin else 'operator')
    # Normalise role
    if role not in ('admin', 'operator', 'viewer'):
        role = 'operator'
    # Keep is_admin in sync
    if role == 'admin':
        is_admin = True
    
    if not username or not password:
        return jsonify({'error': 'Username and password required'}), 400
    
    if username in users:
        return jsonify({'error': 'User already exists'}), 409
    
    if len(username) < 3 or len(username) > 32:
        return jsonify({'error': 'Username must be 3-32 characters'}), 400
    
    if len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400
    
    users[username] = {
        'password': generate_password_hash(password),
        'is_admin': is_admin,
        'role': role,
    }
    
    if save_users(users):
        logger.info(f"User {username} created by {current_user.username} (role={role})")
        return jsonify({'status': 'ok', 'username': username}), 201
    else:
        return jsonify({'error': 'Failed to create user'}), 500


@app.route('/api/users/<username>', methods=['PUT'])
@login_required
@require_admin
def api_update_user(username):
    """Update user (admin only)."""
    users = load_users()
    
    if username not in users:
        return jsonify({'error': 'User not found'}), 404
    
    data = request.get_json()
    
    if 'role' in data:
        role = data['role']
        if role not in ('admin', 'operator', 'viewer'):
            return jsonify({'error': 'Invalid role. Use admin, operator, or viewer'}), 400
        users[username]['role'] = role
        users[username]['is_admin'] = (role == 'admin')
    elif 'is_admin' in data:
        users[username]['is_admin'] = data['is_admin']
        # Keep role in sync
        if data['is_admin']:
            users[username]['role'] = 'admin'
        elif users[username].get('role') == 'admin':
            users[username]['role'] = 'operator'
    
    if 'password' in data and data['password']:
        if len(data['password']) < 8:
            return jsonify({'error': 'Password must be at least 8 characters'}), 400
        users[username]['password'] = generate_password_hash(data['password'])
    
    if save_users(users):
        logger.info(f"User {username} updated by {current_user.username}")
        return jsonify({'status': 'ok'})
    else:
        return jsonify({'error': 'Failed to update user'}), 500


@app.route('/api/users/<username>', methods=['DELETE'])
@login_required
@require_admin
def api_delete_user(username):
    """Delete user (admin only)."""
    users = load_users()
    
    if username == current_user.username:
        return jsonify({'error': 'Cannot delete your own account'}), 400
    
    if username not in users:
        return jsonify({'error': 'User not found'}), 404
    
    del users[username]
    
    if save_users(users):
        logger.info(f"User {username} deleted by {current_user.username}")
        return jsonify({'status': 'ok'})
    else:
        return jsonify({'error': 'Failed to delete user'}), 500


@app.route('/api/change-password', methods=['POST'])
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


@app.route('/console')
@login_required
def console():
    """Main KVM console interface."""
    config = load_config()
    client_ip = get_client_ip()
    audit_log.log_session_start(current_user.username, client_ip)
    return render_template('console.html', config=config)


@app.route('/stream')
@login_required
def stream():
    """Video stream endpoint (MJPEG)."""
    if not video_streamer or not video_streamer.is_running():
        return "Video stream not available", 503
    
    return video_streamer.get_stream_response()


@app.route('/api/keyboard', methods=['POST'])
@login_required
@require_kvm_access
def api_keyboard():
    """Handle keyboard input."""
    if not hid_controller or not hid_controller.connected:
        return jsonify({'error': 'HID device not available'}), 503
    
    data = request.get_json()
    action = data.get('action')
    client_ip = get_client_ip()
    
    try:
        if action == 'key':
            keycode = data.get('keycode')
            pressed = data.get('pressed', True)
            hid_controller.send_key(keycode, pressed)
            audit_log.log_keyboard_input(current_user.username, client_ip, keycode=keycode)
        
        elif action == 'key_with_modifier':
            keycode = data.get('keycode')
            modifiers = data.get('modifiers', 0)
            hid_controller.send_key_with_modifier(keycode, modifiers)
            audit_log.log_keyboard_input(current_user.username, client_ip, keycode=keycode, modifiers=modifiers)
        
        elif action == 'text':
            text = data.get('text', '')
            hid_controller.send_text(text)
            audit_log.log_keyboard_input(current_user.username, client_ip, text=text)
        
        elif action == 'ctrl_alt_del':
            hid_controller.send_ctrl_alt_del()
            audit_log.log_keyboard_input(current_user.username, client_ip, keycode='ctrl_alt_del')
        
        else:
            return jsonify({'error': 'Unknown action'}), 400
        
        return jsonify({'status': 'ok'})
    
    except Exception as e:
        logger.error(f"Keyboard error: {e}")
        return jsonify({'error': 'Keyboard command failed'}), 500


@app.route('/api/mouse', methods=['POST'])
@login_required
@require_kvm_access
def api_mouse():
    """Handle mouse input."""
    if not hid_controller or not hid_controller.connected:
        return jsonify({'error': 'HID device not available'}), 503
    
    data = request.get_json()
    action = data.get('action')
    client_ip = get_client_ip()
    
    try:
        if action == 'move':
            x = data.get('x', 0)
            y = data.get('y', 0)
            wheel = data.get('wheel', 0)
            hid_controller.send_mouse_move(x, y, wheel)
            # Only log every 10th movement to reduce log verbosity
            if data.get('_log_movement'):
                audit_log.log_mouse_movement(current_user.username, client_ip, mode='relative')
        
        elif action == 'move_abs':
            x = data.get('x', 0)
            y = data.get('y', 0)
            wheel = data.get('wheel', 0)
            hid_controller.send_mouse_move_absolute(x, y, wheel)
            if data.get('_log_movement'):
                audit_log.log_mouse_movement(current_user.username, client_ip, mode='absolute')
        
        elif action == 'click':
            button = data.get('button', 'left')
            pressed = data.get('pressed', True)
            hid_controller.send_mouse_click(button, pressed)
            audit_log.log_mouse_click(current_user.username, client_ip, button)
        
        else:
            return jsonify({'error': 'Unknown action'}), 400
        
        return jsonify({'status': 'ok'})
    
    except Exception as e:
        logger.error(f"Mouse error: {e}")
        return jsonify({'error': 'Mouse command failed'}), 500


@app.route('/api/mouse/mode', methods=['GET', 'POST'])
@login_required
@require_kvm_access
def api_mouse_mode():
    """Get or set mouse input mode (absolute/relative)."""
    if request.method == 'GET':
        mode = hid_controller.mouse_mode if hid_controller else 'absolute'
        return jsonify({'mode': mode})
    
    if not hid_controller or not hid_controller.connected:
        return jsonify({'error': 'HID device not available'}), 503
    
    data = request.get_json()
    mode = data.get('mode', '')
    
    if hid_controller.set_mouse_mode(mode):
        return jsonify({'status': 'ok', 'mode': mode})
    return jsonify({'error': 'Invalid mode. Use "absolute" or "relative"'}), 400


@app.route('/api/screenshot', methods=['GET'])
@login_required
def api_screenshot():
    """Capture and return a screenshot of the current video frame."""
    if not video_streamer or not video_streamer.is_running():
        return jsonify({'error': 'Video stream not available'}), 503
    
    frame = video_streamer.capture_screenshot()
    if frame is None:
        return jsonify({'error': 'No frame available yet'}), 503
    
    return Response(
        frame,
        mimetype='image/jpeg',
        headers={
            'Content-Disposition': 'attachment; filename=kvm_screenshot_{}.jpg'.format(
                datetime.utcnow().strftime('%Y%m%d_%H%M%S')
            )
        }
    )


def _handle_ws_message(data):
    """Process a single WebSocket HID message. Returns error string or None."""
    msg_type = data.get('type')
    
    if not hid_controller or not hid_controller.connected:
        return 'HID device not available'
    
    if msg_type == 'key':
        hid_controller.send_key(data.get('keycode', 0), data.get('pressed', True))
    
    elif msg_type == 'key_mod':
        hid_controller.send_key_with_modifier(data.get('keycode', 0), data.get('modifiers', 0))
    
    elif msg_type == 'key_release':
        hid_controller.send_key(data.get('keycode', 0), False)
    
    elif msg_type == 'text':
        hid_controller.send_text(data.get('text', ''))
    
    elif msg_type == 'ctrl_alt_del':
        hid_controller.send_ctrl_alt_del()
    
    elif msg_type == 'mouse_move':
        hid_controller.send_mouse_move(
            data.get('x', 0), data.get('y', 0), data.get('wheel', 0)
        )
    
    elif msg_type == 'mouse_move_abs':
        hid_controller.send_mouse_move_absolute(
            data.get('x', 0), data.get('y', 0), data.get('wheel', 0)
        )
    
    elif msg_type == 'mouse_click':
        hid_controller.send_mouse_click(
            data.get('button', 'left'), data.get('pressed', True)
        )
    
    elif msg_type == 'mouse_mode':
        mode = data.get('mode', '')
        if not hid_controller.set_mouse_mode(mode):
            return 'Invalid mouse mode'
    
    else:
        return f'Unknown message type: {msg_type}'

    # Record input event if recording is active
    if session_recorder and session_recorder.is_recording:
        session_recorder.record_event(data)

    return None


@sock.route('/ws/hid')
def ws_hid(ws):
    """WebSocket endpoint for low-latency HID input."""
    # Verify the user is authenticated via the session cookie
    if not current_user.is_authenticated:
        ws.close(reason='Not authenticated')
        return
    # Verify role - viewers cannot send HID input
    users = load_users()
    user_data = users.get(current_user.username, {})
    user_role = user_data.get('role', 'admin' if user_data.get('is_admin') else 'operator')
    if user_role == 'viewer':
        ws.close(reason='View-only access: HID disabled')
        return
    
    logger.info(f"WebSocket HID connection opened for {current_user.username}")
    
    try:
        while True:
            raw = ws.receive()
            if raw is None:
                break
            
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                ws.send(json.dumps({'error': 'Invalid JSON'}))
                continue
            
            err = _handle_ws_message(data)
            if err:
                ws.send(json.dumps({'error': err}))
    except Exception as e:
        logger.debug(f"WebSocket HID connection closed: {e}")
    finally:
        logger.info(f"WebSocket HID connection closed for {current_user.username}")


@app.route('/pxe')
@login_required
def pxe_page():
    """PXE Boot management page."""
    return render_template('pxe.html')


@app.route('/pxe/boot.ipxe')
def pxe_boot_menu():
    """Serve the iPXE boot menu script (unauthenticated — iPXE cannot send cookies)."""
    if not pxe_server:
        return 'No PXE server configured', 503
    menu = pxe_server.generate_boot_menu()
    return Response(menu, mimetype='text/plain')


@app.route('/pxe/images/<path:name>/file')
def pxe_image_file(name):
    """Serve a boot image file over HTTP (unauthenticated — iPXE cannot send cookies)."""
    if not pxe_server:
        return jsonify({'error': 'PXE not configured'}), 503
    image_path = pxe_server.get_image_path(name)
    if not image_path:
        return jsonify({'error': 'Image not found'}), 404
    return send_file(str(image_path), mimetype='application/octet-stream')


@app.route('/api/pxe/status', methods=['GET'])
@login_required
def api_pxe_status():
    """Get PXE server status."""
    if not pxe_server:
        return jsonify({'error': 'PXE not configured'}), 503
    return jsonify(pxe_server.get_status())


@app.route('/api/pxe/start', methods=['POST'])
@login_required
@require_admin
def api_pxe_start():
    """Start the PXE server (admin only)."""
    if not pxe_server:
        return jsonify({'error': 'PXE not configured'}), 503
    if pxe_server.start():
        logger.info(f"PXE server started by {current_user.username}")
        return jsonify({'status': 'ok'})
    return jsonify({'error': 'Failed to start PXE server'}), 500


@app.route('/api/pxe/stop', methods=['POST'])
@login_required
@require_admin
def api_pxe_stop():
    """Stop the PXE server (admin only)."""
    if not pxe_server:
        return jsonify({'error': 'PXE not configured'}), 503
    pxe_server.stop()
    logger.info(f"PXE server stopped by {current_user.username}")
    return jsonify({'status': 'ok'})


@app.route('/api/pxe/config', methods=['GET', 'POST'])
@login_required
@require_admin
def api_pxe_config():
    """Get or update PXE server configuration (admin only)."""
    if not pxe_server:
        return jsonify({'error': 'PXE not configured'}), 503

    if request.method == 'GET':
        return jsonify(pxe_server.to_config_dict())

    data = request.get_json()
    allowed = ('interface', 'dhcp_range', 'server_ip', 'base_dir',
                'netbootxyz_enabled', 'netbootxyz_url', 'netbootxyz_efi_url')
    pxe_cfg = {}
    for key in allowed:
        if key in data:
            pxe_cfg[key] = data[key]  # preserve bool type for netbootxyz_enabled
    # string-coerce the path/IP fields
    for key in ('interface', 'dhcp_range', 'server_ip', 'base_dir',
                'netbootxyz_url', 'netbootxyz_efi_url'):
        if key in pxe_cfg:
            pxe_cfg[key] = str(pxe_cfg[key]).strip()

    pxe_server.apply_config(pxe_cfg)

    # Persist into main config.json
    config = load_config()
    config['pxe'] = pxe_server.to_config_dict()
    save_config(config)

    # Regenerate dnsmasq config (takes effect on next start/restart)
    pxe_server.write_config()

    logger.info(f"PXE config updated by {current_user.username}")
    return jsonify({'status': 'ok', 'config': pxe_server.to_config_dict()})


@app.route('/api/pxe/images', methods=['GET'])
@login_required
def api_pxe_images():
    """List available PXE boot images."""
    if not pxe_server:
        return jsonify({'error': 'PXE not configured'}), 503
    return jsonify({'images': pxe_server.list_images()})


@app.route('/api/pxe/images/upload', methods=['POST'])
@login_required
@require_admin
def api_pxe_upload():
    """Upload a boot image (admin only)."""
    if not pxe_server:
        return jsonify({'error': 'PXE not configured'}), 503

    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'No filename'}), 400

    filename = PXEServer.sanitize_filename(f.filename)
    result = pxe_server.save_image(filename, f.stream)
    if result:
        logger.info(f"PXE image uploaded by {current_user.username}: {filename}")
        return jsonify({'status': 'ok', 'image': result})
    return jsonify({'error': 'Failed to save image'}), 500


@app.route('/api/pxe/images/<name>', methods=['DELETE'])
@login_required
@require_admin
def api_pxe_delete_image(name):
    """Delete a boot image (admin only)."""
    if not pxe_server:
        return jsonify({'error': 'PXE not configured'}), 503

    safe_name = PXEServer.sanitize_filename(name)
    if pxe_server.delete_image(safe_name):
        logger.info(f"PXE image deleted by {current_user.username}: {safe_name}")
        return jsonify({'status': 'ok'})
    return jsonify({'error': 'Image not found or could not be deleted'}), 404


@app.route('/api/pxe/dependencies', methods=['GET'])
@login_required
def api_pxe_dependencies():
    """Check PXE system dependencies (dnsmasq, iPXE binaries)."""
    if not pxe_server:
        return jsonify({'error': 'PXE not configured'}), 503
    return jsonify(pxe_server.check_dependencies())


@app.route('/api/pxe/catalog', methods=['GET'])
@login_required
def api_pxe_catalog():
    """Return the full OS catalog with an 'enabled' field per entry."""
    from boot_catalog import BOOT_CATALOG
    enabled = set(pxe_server.enabled_catalog_ids) if pxe_server else set()
    catalog = [{**entry, 'enabled': entry['id'] in enabled} for entry in BOOT_CATALOG]
    return jsonify({'catalog': catalog})


@app.route('/api/pxe/catalog/enabled', methods=['POST'])
@login_required
@require_admin
def api_pxe_catalog_enabled():
    """Set which catalog entry IDs are active in the boot menu (admin only)."""
    if not pxe_server:
        return jsonify({'error': 'PXE not configured'}), 503
    data = request.get_json() or {}
    ids = [str(i) for i in data.get('enabled_ids', [])]
    pxe_server.enabled_catalog_ids = ids
    cfg = load_config()
    cfg['pxe'] = pxe_server.to_config_dict()
    save_config(cfg)
    pxe_server.write_config()
    return jsonify({'status': 'ok', 'enabled_ids': ids})


@app.route('/api/pxe/menu-preview', methods=['GET'])
@login_required
def api_pxe_menu_preview():
    """Return the current iPXE boot script for the UI boot-menu preview."""
    if not pxe_server:
        return jsonify({'error': 'PXE not configured'}), 503
    return jsonify({'script': pxe_server.generate_boot_menu()})


@app.route('/api/ping', methods=['GET'])
@login_required
def api_ping():
    """Lightweight ping for client-side latency measurement."""
    return jsonify({'pong': True, 'ts': datetime.utcnow().isoformat()})


@app.route('/api/video/stats', methods=['GET'])
@login_required
def api_video_stats():
    """Get current video stream statistics (FPS, bandwidth, resolution)."""
    if not video_streamer or not video_streamer.running:
        return jsonify({'error': 'Video not available'}), 503
    return jsonify(video_streamer.get_stream_stats())


@app.route('/api/video/quality', methods=['POST'])
@login_required
def api_video_quality():
    """Change video quality on-the-fly (restarts the stream)."""
    if not video_streamer:
        return jsonify({'error': 'Video not available'}), 503

    data = request.get_json()
    resolution = data.get('resolution')
    framerate = data.get('framerate')
    bitrate = data.get('bitrate')

    # Validate
    if resolution and not re.match(r'^\d{3,4}x\d{3,4}$', str(resolution)):
        return jsonify({'error': 'Invalid resolution format'}), 400
    if framerate is not None:
        framerate = int(framerate)
        if framerate < 1 or framerate > 60:
            return jsonify({'error': 'Framerate must be 1-60'}), 400
    if bitrate and not re.match(r'^\d+k$', str(bitrate)):
        return jsonify({'error': 'Invalid bitrate format (e.g. 2000k)'}), 400

    success = video_streamer.update_settings(resolution=resolution, framerate=framerate, bitrate=bitrate)

    # Persist to config
    config = load_config()
    if resolution:
        config['resolution'] = resolution
    if framerate:
        config['framerate'] = framerate
    if bitrate:
        config['bitrate'] = bitrate
    save_config(config)

    logger.info(f"Video quality changed by {current_user.username}: res={resolution} fps={framerate} br={bitrate}")
    return jsonify({'status': 'ok' if success else 'error', 'stats': video_streamer.get_stream_stats()})


@app.route('/dashboard')
@login_required
def dashboard_page():
    """System dashboard page - ensure password change is completed."""
    # Security check: ensure user has completed password change if required
    username = current_user.username
    users = load_users()
    user_data = users.get(username, {})
    
    if user_data.get('requires_password_change', False):
        logger.warning(f"User {username} attempted to access dashboard without changing password")
        return redirect(url_for('change_password'))
    
    return render_template('dashboard.html')


@app.route('/api/system/stats', methods=['GET'])
@login_required
def api_system_stats():
    """Get system resource stats (CPU, RAM, disk, temp, network)."""
    return jsonify(system_monitor.get_all())


@app.route('/api/recording/start', methods=['POST'])
@login_required
@require_admin
def api_recording_start():
    """Start session recording (admin only)."""
    if not session_recorder:
        return jsonify({'error': 'Recorder not configured'}), 503
    rec_id = session_recorder.start_recording(username=current_user.username)
    if rec_id:
        if video_streamer:
            video_streamer.set_record_callback(session_recorder.record_frame)
        logger.info(f"Recording started by {current_user.username}: {rec_id}")
        return jsonify({'status': 'ok', 'recording_id': rec_id})
    return jsonify({'error': 'Failed to start recording'}), 500


@app.route('/api/recording/stop', methods=['POST'])
@login_required
@require_admin
def api_recording_stop():
    """Stop session recording (admin only)."""
    if not session_recorder:
        return jsonify({'error': 'Recorder not configured'}), 503
    if video_streamer:
        video_streamer.set_record_callback(None)
    rec_id = session_recorder.stop_recording()
    if rec_id:
        logger.info(f"Recording stopped by {current_user.username}: {rec_id}")
        return jsonify({'status': 'ok', 'recording_id': rec_id})
    return jsonify({'error': 'No active recording'}), 400


@app.route('/api/recording/status', methods=['GET'])
@login_required
def api_recording_status():
    """Get current recording status."""
    if not session_recorder:
        return jsonify({'recording': False})
    return jsonify({
        'recording': session_recorder.is_recording,
        'recording_id': session_recorder._current_id,
    })


@app.route('/recordings')
@login_required
def recordings_page():
    """Session recording browser and replay page."""
    return render_template('recordings.html')


@app.route('/api/recordings', methods=['GET'])
@login_required
def api_recordings_list():
    """List all recordings."""
    if not session_recorder:
        return jsonify({'recordings': []})
    return jsonify({'recordings': session_recorder.list_recordings()})


@app.route('/api/recordings/<recording_id>', methods=['GET', 'DELETE'])
@login_required
def api_recording_detail(recording_id):
    """Get or delete a recording."""
    if not session_recorder:
        return jsonify({'error': 'Recorder not configured'}), 503

    if request.method == 'DELETE':
        if session_recorder.delete_recording(recording_id):
            return jsonify({'status': 'ok'})
        return jsonify({'error': 'Not found'}), 404

    meta = session_recorder.get_recording(recording_id)
    if not meta:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(meta)


@app.route('/api/recordings/<recording_id>/frame/<int:frame_num>')
@login_required
def api_recording_frame(recording_id, frame_num):
    """Get a specific frame from a recording as JPEG."""
    if not session_recorder:
        return jsonify({'error': 'Recorder not configured'}), 503
    data = session_recorder.get_frame(recording_id, frame_num)
    if data is None:
        return jsonify({'error': 'Frame not found'}), 404
    return Response(data, mimetype='image/jpeg')


@app.route('/api/recordings/<recording_id>/events')
@login_required
def api_recording_events(recording_id):
    """Get input events for a recording."""
    if not session_recorder:
        return jsonify({'error': 'Recorder not configured'}), 503
    events = session_recorder.get_events(recording_id)
    return jsonify({'events': events})


# ── Screenshot storage ──────────────────────────────────────────────────

def _screenshots_dir():
    """Return the screenshots storage directory Path, creating it if needed."""
    config = load_config()
    rec_config = config.get('recording', {})
    base = Path(rec_config.get('recordings_dir', '/var/lib/kvm/recordings'))
    d = base.parent / 'screenshots'
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


@app.route('/api/screenshots', methods=['GET'])
@login_required
def api_screenshots_list():
    """List all saved screenshots."""
    d = _screenshots_dir()
    shots = []
    try:
        for f in sorted(d.glob('*.jpg'), key=lambda p: p.stat().st_mtime, reverse=True):
            stat = f.stat()
            shots.append({
                'filename': f.name,
                'size': stat.st_size,
                'taken_at': datetime.utcfromtimestamp(stat.st_mtime).isoformat(),
            })
    except Exception as e:
        logger.error(f"Failed to list screenshots: {e}")
    return jsonify({'screenshots': shots})


@app.route('/api/screenshots', methods=['POST'])
@login_required
@require_operator
def api_screenshots_save():
    """Capture the current video frame and save it to disk."""
    if not video_streamer or not video_streamer.is_running():
        return jsonify({'error': 'Video stream not available'}), 503
    frame = video_streamer.capture_screenshot()
    if frame is None:
        return jsonify({'error': 'No frame available yet'}), 503
    d = _screenshots_dir()
    filename = 'screenshot_{}.jpg'.format(datetime.utcnow().strftime('%Y%m%d_%H%M%S'))
    path = d / filename
    try:
        path.write_bytes(frame)
    except Exception as e:
        logger.error(f"Failed to save screenshot: {e}")
        return jsonify({'error': 'Failed to save screenshot'}), 500
    logger.info(f"Screenshot saved by {current_user.username}: {filename}")
    return jsonify({'status': 'ok', 'filename': filename}), 201


@app.route('/api/screenshots/<filename>', methods=['GET'])
@login_required
def api_screenshot_file(filename):
    """Serve a saved screenshot image."""
    if not re.match(r'^screenshot_\d{8}_\d{6}\.jpg$', filename):
        return jsonify({'error': 'Invalid filename'}), 400
    d = _screenshots_dir()
    path = d / filename
    if not path.exists():
        return jsonify({'error': 'Not found'}), 404
    return send_file(str(path), mimetype='image/jpeg')


@app.route('/api/screenshots/<filename>', methods=['DELETE'])
@login_required
@require_operator
def api_screenshot_delete(filename):
    """Delete a saved screenshot."""
    if not re.match(r'^screenshot_\d{8}_\d{6}\.jpg$', filename):
        return jsonify({'error': 'Invalid filename'}), 400
    d = _screenshots_dir()
    path = d / filename
    try:
        path.unlink()
    except FileNotFoundError:
        return jsonify({'error': 'Not found'}), 404
    except Exception as e:
        logger.error(f"Failed to delete screenshot: {e}")
        return jsonify({'error': 'Delete failed'}), 500
    logger.info(f"Screenshot deleted by {current_user.username}: {filename}")
    return jsonify({'status': 'ok'})


@app.route('/api/cert/info', methods=['GET'])
@login_required
@require_admin
def api_cert_info():
    """Get current SSL certificate details."""
    if not cert_manager:
        return jsonify({'error': 'Cert manager not configured'}), 503
    info = cert_manager.get_cert_info()
    return jsonify({
        'has_certificate': cert_manager.has_certificate(),
        'info': info,
    })


@app.route('/api/cert/generate', methods=['POST'])
@login_required
@require_admin
def api_cert_generate():
    """Generate a self-signed certificate."""
    if not cert_manager:
        return jsonify({'error': 'Cert manager not configured'}), 503

    data = request.get_json() or {}
    cn = data.get('common_name', 'kvm-over-ip')
    days = int(data.get('days', 365))
    san = data.get('san_names')

    if cert_manager.generate_self_signed(common_name=cn, days=days, san_names=san):
        logger.info(f"Self-signed cert generated by {current_user.username}: CN={cn}")
        return jsonify({'status': 'ok', 'info': cert_manager.get_cert_info()})
    return jsonify({'error': 'Certificate generation failed'}), 500


@app.route('/api/cert/upload', methods=['POST'])
@login_required
@require_admin
def api_cert_upload():
    """Upload a custom certificate and key."""
    if not cert_manager:
        return jsonify({'error': 'Cert manager not configured'}), 503

    cert_file = request.files.get('cert')
    key_file = request.files.get('key')
    if not cert_file or not key_file:
        return jsonify({'error': 'Both cert and key files required'}), 400

    result = cert_manager.upload_certificate(cert_file.read(), key_file.read())
    if result is True:
        logger.info(f"Custom certificate uploaded by {current_user.username}")
        return jsonify({'status': 'ok', 'info': cert_manager.get_cert_info()})
    return jsonify({'error': result}), 400


@app.route('/api/cert/letsencrypt', methods=['POST'])
@login_required
@require_admin
def api_cert_letsencrypt():
    """Request a Let's Encrypt certificate."""
    if not cert_manager:
        return jsonify({'error': 'Cert manager not configured'}), 503

    data = request.get_json() or {}
    domain = data.get('domain', '')
    email = data.get('email', '')

    if not domain:
        return jsonify({'error': 'Domain is required'}), 400

    result = cert_manager.request_letsencrypt(domain, email=email or None)
    if result is True:
        logger.info(f"Let's Encrypt cert obtained by {current_user.username}: {domain}")
        return jsonify({'status': 'ok', 'info': cert_manager.get_cert_info()})
    return jsonify({'error': result}), 500


@app.route('/api/cert/delete', methods=['POST'])
@login_required
@require_admin
def api_cert_delete():
    """Delete the current certificate (revert to HTTP)."""
    if not cert_manager:
        return jsonify({'error': 'Cert manager not configured'}), 503
    cert_manager.delete_certificate()
    logger.info(f"Certificate deleted by {current_user.username}")
    return jsonify({'status': 'ok', 'message': 'Certificate removed. Restart server to use HTTP.'})


@app.route('/api/cert/renew', methods=['POST'])
@login_required
@require_admin
def api_cert_renew():
    """Manually trigger Let's Encrypt certificate renewal."""
    if not cert_manager:
        return jsonify({'error': 'Cert manager not configured'}), 503
    if not cert_manager.has_certificate():
        return jsonify({'error': 'No certificate to renew'}), 400
    days_left = cert_manager.check_expiry_days()
    result = cert_manager.auto_renew()
    if result is True:
        logger.info(f"Certificate manually renewed by {current_user.username}")
        return jsonify({'status': 'ok', 'info': cert_manager.get_cert_info(), 'days_was': days_left})
    return jsonify({'error': result or 'Renewal failed'}), 500


@app.route('/api/edid/status', methods=['GET'])
@login_required
def api_edid_status():
    """Get EDID emulation status and available presets."""
    if not edid_manager:
        return jsonify({'error': 'EDID manager not configured'}), 503
    return jsonify(edid_manager.get_status())


@app.route('/api/edid/preset', methods=['POST'])
@login_required
@require_admin
def api_edid_preset():
    """Write an EDID preset to the capture device."""
    if not edid_manager:
        return jsonify({'error': 'EDID manager not configured'}), 503

    data = request.get_json() or {}
    preset = data.get('preset', '')
    result = edid_manager.write_preset(preset)
    if result is True:
        logger.info(f"EDID preset '{preset}' applied by {current_user.username}")
        return jsonify({'status': 'ok'})
    return jsonify({'error': result}), 400


@app.route('/api/edid/upload', methods=['POST'])
@login_required
@require_admin
def api_edid_upload():
    """Upload a raw EDID binary file."""
    if not edid_manager:
        return jsonify({'error': 'EDID manager not configured'}), 503

    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'No file provided'}), 400

    result = edid_manager.upload_edid(f.read())
    if result is True:
        logger.info(f"Custom EDID uploaded by {current_user.username}")
        return jsonify({'status': 'ok'})
    return jsonify({'error': result}), 400


@app.route('/api/edid/clear', methods=['POST'])
@login_required
@require_admin
def api_edid_clear():
    """Clear/reset the EDID on the capture device."""
    if not edid_manager:
        return jsonify({'error': 'EDID manager not configured'}), 503
    result = edid_manager.clear_edid()
    if result is True:
        return jsonify({'status': 'ok'})
    return jsonify({'error': result}), 400


@app.route('/api/syslog', methods=['GET', 'POST'])
@login_required
@require_admin
def api_syslog():
    """Get or update remote syslog configuration."""
    config = load_config()

    if request.method == 'GET':
        return jsonify(config.get('syslog', {'host': '', 'port': 514}))

    data = request.get_json() or {}
    config['syslog'] = {
        'host': str(data.get('host', '')).strip(),
        'port': int(data.get('port', 514)),
    }
    save_config(config)
    setup_remote_syslog()
    logger.info(f"Syslog config updated by {current_user.username}")
    return jsonify({'status': 'ok', 'syslog': config['syslog']})


@app.route('/api/ip-acl', methods=['GET', 'POST'])
@login_required
@require_admin
def api_ip_acl():
    """Get or update IP access control list configuration."""
    config = load_config()

    if request.method == 'GET':
        return jsonify(config.get('ip_acl', {
            'enabled': False, 'mode': 'whitelist',
            'whitelist': [], 'blacklist': []
        }))

    data = request.get_json() or {}
    acl = config.get('ip_acl', {})
    if 'enabled' in data:
        acl['enabled'] = bool(data['enabled'])
    if 'mode' in data and data['mode'] in ('whitelist', 'blacklist'):
        acl['mode'] = data['mode']
    if 'whitelist' in data and isinstance(data['whitelist'], list):
        valid = []
        for entry in data['whitelist']:
            entry = str(entry).strip()
            if not entry:
                continue
            try:
                if '/' in entry:
                    ipaddress.ip_network(entry, strict=False)
                else:
                    ipaddress.ip_address(entry)
                valid.append(entry)
            except ValueError:
                return jsonify({'error': f'Invalid IP/CIDR: {entry}'}), 400
        acl['whitelist'] = valid
    if 'blacklist' in data and isinstance(data['blacklist'], list):
        valid = []
        for entry in data['blacklist']:
            entry = str(entry).strip()
            if not entry:
                continue
            try:
                if '/' in entry:
                    ipaddress.ip_network(entry, strict=False)
                else:
                    ipaddress.ip_address(entry)
                valid.append(entry)
            except ValueError:
                return jsonify({'error': f'Invalid IP/CIDR: {entry}'}), 400
        acl['blacklist'] = valid

    config['ip_acl'] = acl
    save_config(config)
    logger.info(f"IP ACL config updated by {current_user.username}: mode={acl.get('mode')}, enabled={acl.get('enabled')}")
    return jsonify({'status': 'ok', 'ip_acl': acl})


@app.route('/api/macros', methods=['GET'])
@login_required
def api_macros_list():
    """List all macros."""
    if not macro_manager:
        return jsonify({'macros': []})
    return jsonify({'macros': macro_manager.list_macros()})


@app.route('/api/macros/<macro_id>', methods=['GET', 'DELETE'])
@login_required
def api_macro_detail(macro_id):
    """Get or delete a macro."""
    if not macro_manager:
        return jsonify({'error': 'Macros not configured'}), 503

    if request.method == 'DELETE':
        if macro_manager.delete_macro(macro_id):
            return jsonify({'status': 'ok'})
        return jsonify({'error': 'Cannot delete (builtin or not found)'}), 400

    macro = macro_manager.get_macro(macro_id)
    if not macro:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(macro)


@app.route('/api/macros', methods=['POST'])
@login_required
def api_macro_create():
    """Create or update a macro."""
    if not macro_manager:
        return jsonify({'error': 'Macros not configured'}), 503
    data = request.get_json() or {}
    result = macro_manager.create_macro(data)
    if isinstance(result, str):
        return jsonify({'error': result}), 400
    return jsonify({'status': 'ok', 'macro': result})


@app.route('/api/macros/<macro_id>/execute', methods=['POST'])
@login_required
def api_macro_execute(macro_id):
    """Execute a macro."""
    if not macro_manager:
        return jsonify({'error': 'Macros not configured'}), 503
    if not hid_controller or not hid_controller.connected:
        return jsonify({'error': 'HID device not available'}), 503

    import threading as _threading
    def run():
        macro_manager.execute_macro(macro_id, hid_controller)
    _threading.Thread(target=run, daemon=True).start()
    logger.info(f"Macro '{macro_id}' executed by {current_user.username}")
    return jsonify({'status': 'ok'})


@app.route('/api/macros/abort', methods=['POST'])
@login_required
def api_macro_abort():
    """Abort a running macro."""
    if macro_manager:
        macro_manager.abort_macro()
    return jsonify({'status': 'ok'})


@app.route('/api/notifications/config', methods=['GET', 'POST'])
@login_required
@require_admin
def api_notifications_config():
    """Get or update notification configuration."""
    config = load_config()

    if request.method == 'GET':
        nc = config.get('notifications', {})
        # Mask SMTP password
        if nc.get('email', {}).get('smtp_pass'):
            nc['email']['smtp_pass'] = '********'
        return jsonify(nc)

    data = request.get_json() or {}
    nc = config.get('notifications', {})

    if 'email' in data:
        email = nc.get('email', {})
        for k in ('enabled', 'smtp_host', 'smtp_port', 'smtp_user', 'smtp_tls', 'from_addr'):
            if k in data['email']:
                email[k] = data['email'][k]
        if 'smtp_pass' in data['email'] and data['email']['smtp_pass'] != '********':
            email['smtp_pass'] = data['email']['smtp_pass']
        if 'to_addrs' in data['email']:
            email['to_addrs'] = [a.strip() for a in data['email']['to_addrs'] if a.strip()]
        nc['email'] = email

    if 'webhook' in data:
        wh = nc.get('webhook', {})
        for k in ('enabled', 'url', 'headers'):
            if k in data['webhook']:
                wh[k] = data['webhook'][k]
        nc['webhook'] = wh

    if 'events' in data:
        nc['events'] = data['events']
    if 'temperature_threshold' in data:
        nc['temperature_threshold'] = int(data['temperature_threshold'])

    config['notifications'] = nc
    save_config(config)
    logger.info(f"Notification config updated by {current_user.username}")
    return jsonify({'status': 'ok'})


@app.route('/api/notifications/test/email', methods=['POST'])
@login_required
@require_admin
def api_notifications_test_email():
    """Send a test email notification."""
    if not notification_manager:
        return jsonify({'error': 'Notifications not configured'}), 503
    config = load_config()
    email_cfg = config.get('notifications', {}).get('email', {})
    result = notification_manager.test_email(email_cfg)
    if result is True:
        return jsonify({'status': 'ok'})
    return jsonify({'error': result}), 400


@app.route('/api/notifications/test/webhook', methods=['POST'])
@login_required
@require_admin
def api_notifications_test_webhook():
    """Send a test webhook notification."""
    if not notification_manager:
        return jsonify({'error': 'Notifications not configured'}), 503
    config = load_config()
    webhook_cfg = config.get('notifications', {}).get('webhook', {})
    result = notification_manager.test_webhook(webhook_cfg)
    if result is True:
        return jsonify({'status': 'ok'})
    return jsonify({'error': result}), 400


@app.route('/auth/oidc/login')
def oidc_login():
    """Redirect to the OIDC provider's authorization endpoint."""
    if not oidc_auth or not oidc_auth.is_enabled:
        return redirect(url_for('login'))
    state = secrets.token_urlsafe(32)
    session['oidc_state'] = state
    redirect_uri = url_for('oidc_callback', _external=True)
    try:
        auth_url = oidc_auth.get_authorization_url(redirect_uri, state)
    except Exception as e:
        logger.error(f"OIDC authorization URL error: {e}")
        return render_template('login.html', error='SSO configuration error'), 500
    return redirect(auth_url)


@app.route('/auth/oidc/callback')
def oidc_callback():
    """Handle the OIDC provider's authorization code callback."""
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
        redirect_uri = url_for('oidc_callback', _external=True)
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
        return redirect(url_for('verify_2fa'))

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


@app.route('/api/oidc/config', methods=['GET', 'POST'])
@login_required
@require_admin
def api_oidc_config():
    """Get or update OIDC configuration."""
    config = load_config()

    if request.method == 'GET':
        oc = config.get('oidc', {}).copy()
        if oc.get('client_secret'):
            oc['client_secret'] = '********'
        oc['available'] = oidc_auth.is_available if oidc_auth else False
        return jsonify(oc)

    data = request.get_json() or {}
    oc = config.get('oidc', {})
    for k in ('enabled', 'issuer_url', 'client_id', 'scope',
              'username_claim', 'admin_claim', 'admin_claim_value', 'allowed_groups'):
        if k in data:
            oc[k] = data[k]
    if 'client_secret' in data and data['client_secret'] != '********':
        oc['client_secret'] = data['client_secret']

    config['oidc'] = oc
    save_config(config)
    if oidc_auth:
        oidc_auth.update_config(oc)
    logger.info(f"OIDC config updated by {current_user.username}")
    return jsonify({'status': 'ok'})


@app.route('/api/oidc/test', methods=['POST'])
@login_required
@require_admin
def api_oidc_test():
    """Test OIDC discovery URL connectivity."""
    if not oidc_auth:
        return jsonify({'error': 'OIDC not configured'}), 503
    if not oidc_auth.is_available:
        return jsonify({'error': 'authlib not installed. Run: pip install authlib requests'}), 503
    result = oidc_auth.test_discovery()
    if result.get('ok'):
        return jsonify({'status': 'ok', 'info': result})
    return jsonify({'error': result.get('error', 'Discovery failed')}), 400


@app.route('/api/backup/export', methods=['POST'])
@login_required
@require_admin
def api_backup_export():
    """Export configuration backup as a ZIP archive."""
    if not backup_manager:
        return jsonify({'error': 'Backup not configured'}), 503

    data = request.get_json() or {}
    password = data.get('password', '')

    result = backup_manager.create_backup(password=password or None)
    if isinstance(result, str):
        return jsonify({'error': result}), 500

    from io import BytesIO
    ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    return Response(
        result,
        mimetype='application/zip',
        headers={'Content-Disposition': f'attachment; filename=kvm_backup_{ts}.zip'}
    )


@app.route('/api/backup/import', methods=['POST'])
@login_required
@require_admin
def api_backup_import():
    """Import configuration from a backup archive."""
    if not backup_manager:
        return jsonify({'error': 'Backup not configured'}), 503

    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'No file provided'}), 400

    password = request.form.get('password', '')
    archive_bytes = f.read()

    result = backup_manager.restore_backup(archive_bytes, password=password or None)
    if isinstance(result, str):
        return jsonify({'error': result}), 400

    logger.info(f"Backup restored by {current_user.username}: {result}")
    return jsonify({'status': 'ok', 'result': result})


@app.route('/api/backup/files', methods=['GET'])
@login_required
@require_admin
def api_backup_files():
    """List config files that would be included in a backup."""
    if not backup_manager:
        return jsonify({'files': []})
    return jsonify({'files': backup_manager.list_config_files()})


@app.route('/api/update/check', methods=['GET'])
@login_required
@require_admin
def api_update_check():
    """Check for software updates via git."""
    import subprocess as _sp
    try:
        # Fetch latest
        _sp.run(['git', 'fetch'], capture_output=True, timeout=30, cwd=os.path.dirname(__file__) or '.')
        result = _sp.run(
            ['git', 'log', 'HEAD..origin/main', '--oneline'],
            capture_output=True, text=True, timeout=10,
            cwd=os.path.dirname(__file__) or '.',
        )
        commits = result.stdout.strip().splitlines() if result.stdout.strip() else []
        current = _sp.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            capture_output=True, text=True, timeout=5,
            cwd=os.path.dirname(__file__) or '.',
        )
        return jsonify({
            'updates_available': len(commits) > 0,
            'pending_commits': commits,
            'current_commit': current.stdout.strip(),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/update/apply', methods=['POST'])
@login_required
@require_admin
def api_update_apply():
    """Pull latest code from git and signal for restart."""
    import subprocess as _sp
    try:
        result = _sp.run(
            ['git', 'pull', '--ff-only'],
            capture_output=True, text=True, timeout=60,
            cwd=os.path.dirname(__file__) or '.',
        )
        if result.returncode != 0:
            return jsonify({'error': f'git pull failed: {result.stderr.strip()}'}), 500
        logger.info(f"Software update applied by {current_user.username}: {result.stdout.strip()}")
        return jsonify({'status': 'ok', 'output': result.stdout.strip(), 'message': 'Update applied. Restart the service to activate.'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/config', methods=['GET', 'POST'])
@login_required
def api_config():
    """Get or update configuration (admin only for POST)."""
    if request.method == 'GET':
        config = load_config()
        return jsonify(config)
    
    users = load_users()
    if not users.get(current_user.username, {}).get('is_admin', False):
        return jsonify({'error': 'Admin access required'}), 403
    
    data = request.get_json()
    config = load_config()
    
    allowed_keys = ['video_device', 'hid_device', 'resolution', 'framerate', 'bitrate', 'idle_timeout']
    for key in allowed_keys:
        if key in data:
            if not validate_config_value(key, data[key]):
                return jsonify({'error': f'Invalid value for {key}'}), 400
            config[key] = data[key]
    
    if save_config(config):
        logger.info(f"Configuration updated by {current_user.username}")
        return jsonify({'status': 'ok', 'config': config})
    else:
        return jsonify({'error': 'Failed to save config'}), 500


@app.route('/api/status', methods=['GET'])
@login_required
def api_status():
    """Get system status."""
    config = load_config()
    users = load_users()
    user_data = users.get(current_user.username, {})
    role = user_data.get('role', 'admin' if user_data.get('is_admin') else 'operator')
    return jsonify({
        'user': current_user.username,
        'is_admin': user_data.get('is_admin', False),
        'role': role,
        'hid_connected': hid_controller.connected if hid_controller else False,
        'video_running': video_streamer.is_running() if video_streamer else False,
        'mouse_mode': hid_controller.mouse_mode if hid_controller else 'absolute',
        'recording': session_recorder.is_recording if session_recorder else False,
        'idle_timeout': config.get('idle_timeout', 900),
        'stream_stats': video_streamer.get_stream_stats() if video_streamer and video_streamer.is_running() else None,
        'csrf_token': generate_csrf_token(),
        'timestamp': datetime.utcnow().isoformat()
    })


@app.route('/api/reconnect', methods=['POST'])
@login_required
@require_admin
def api_reconnect():
    """Reconnect HID and/or video devices."""
    data = request.get_json() or {}
    results = {}
    
    if data.get('hid', False):
        if hid_controller:
            hid_controller.disconnect()
        results['hid'] = init_hid()
    
    if data.get('video', False):
        if video_streamer:
            video_streamer.stop()
        results['video'] = init_video()
    
    return jsonify({'status': 'ok', 'results': results})


@app.route('/api/2fa/setup', methods=['POST'])
@login_required
def api_2fa_setup():
    """Generate 2FA secret and QR code."""
    username = current_user.username
    secret, uri = totp_manager.generate_secret(username)
    qr_code = totp_manager.get_qr_code(username, secret)
    session['pending_2fa_secret'] = secret
    return jsonify({'qr_code': qr_code, 'secret': secret, 'uri': uri})


@app.route('/api/2fa/confirm', methods=['POST'])
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


@app.route('/api/2fa/disable', methods=['POST'])
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


@app.route('/api/2fa/status', methods=['GET'])
@login_required
def api_2fa_status():
    """Get 2FA status for current user."""
    return jsonify(totp_manager.get_user_2fa_status(current_user.username))


@app.route('/api/audit-log', methods=['GET'])
@login_required
@require_admin
def api_audit_log():
    """Get recent login attempts (admin only)."""
    users = load_users()
    logs = {}
    for username in users:
        logs[username] = session_manager.get_login_attempts(username, hours=72)
    return jsonify({'audit_log': logs})


# Wake-on-LAN API
@app.route('/api/wol/targets', methods=['GET'])
@login_required
def api_wol_targets():
    """List all WOL targets."""
    if not wol_manager:
        return jsonify({'error': 'WOL not configured'}), 503
    
    targets = wol_manager.list_targets()
    return jsonify({'targets': targets})


@app.route('/api/wol/add', methods=['POST'])
@login_required
@require_admin
def api_wol_add_target():
    """Add a WOL target device (admin only)."""
    if not wol_manager:
        return jsonify({'error': 'WOL not configured'}), 503
    
    data = request.get_json()
    name = data.get('name', '').strip()
    mac_address = data.get('mac_address', '').strip()
    broadcast_ip = data.get('broadcast_ip', '255.255.255.255')
    port = data.get('port', 9)
    
    if not name or not mac_address:
        return jsonify({'error': 'Name and MAC address required'}), 400
    
    try:
        result = wol_manager.add_target(name, mac_address, broadcast_ip, port)
        if result is True:
            audit_log.log('wol_add_target', username=current_user.username, ip_address=get_client_ip(),
                         details={'name': name, 'mac': mac_address})
            logger.info(f"WOL target added by {current_user.username}: {name}")
            return jsonify({'status': 'ok', 'name': name}), 201
        else:
            return jsonify({'error': result}), 400
    except Exception as e:
        logger.error(f"WOL add target failed: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/wol/send', methods=['POST'])
@login_required
@require_admin
def api_wol_send():
    """Send WOL magic packet to a target (admin only)."""
    if not wol_manager:
        return jsonify({'error': 'WOL not configured'}), 503
    
    data = request.get_json()
    target_name = data.get('target_name', '').strip()
    
    if not target_name:
        return jsonify({'error': 'Target name required'}), 400
    
    try:
        result = wol_manager.send_wol(target_name)
        if result is True:
            audit_log.log('wol_send', username=current_user.username, ip_address=get_client_ip(),
                         details={'target': target_name})
            logger.info(f"WOL packet sent by {current_user.username} to {target_name}")
            return jsonify({'status': 'ok', 'sent': target_name})
        else:
            return jsonify({'error': result}), 400
    except Exception as e:
        logger.error(f"WOL send failed: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/wol/send-by-mac', methods=['POST'])
@login_required
@require_admin
def api_wol_send_by_mac():
    """Send WOL magic packet by MAC address (admin only)."""
    if not wol_manager:
        return jsonify({'error': 'WOL not configured'}), 503
    
    data = request.get_json()
    mac_address = data.get('mac_address', '').strip()
    broadcast_ip = data.get('broadcast_ip', '255.255.255.255')
    port = data.get('port', 9)
    
    if not mac_address:
        return jsonify({'error': 'MAC address required'}), 400
    
    try:
        result = wol_manager.send_wol_by_mac(mac_address, broadcast_ip, port)
        if result is True:
            audit_log.log('wol_send_by_mac', username=current_user.username, ip_address=get_client_ip(),
                         details={'mac': mac_address})
            logger.info(f"WOL packet sent by {current_user.username} to {mac_address}")
            return jsonify({'status': 'ok', 'sent': mac_address})
        else:
            return jsonify({'error': result}), 400
    except Exception as e:
        logger.error(f"WOL send by MAC failed: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/wol/remove/<target_name>', methods=['DELETE'])
@login_required
@require_admin
def api_wol_remove_target(target_name):
    """Remove a WOL target (admin only)."""
    if not wol_manager:
        return jsonify({'error': 'WOL not configured'}), 503
    
    try:
        if wol_manager.targets.pop(target_name, None):
            audit_log.log('wol_remove_target', username=current_user.username, ip_address=get_client_ip(),
                         details={'name': target_name})
            logger.info(f"WOL target removed by {current_user.username}: {target_name}")
            return jsonify({'status': 'ok'})
        else:
            return jsonify({'error': 'Target not found'}), 404
    except Exception as e:
        logger.error(f"WOL remove target failed: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/wol/schedules', methods=['GET'])
@login_required
@require_operator
def api_wol_schedules_list():
    """List all WoL schedules."""
    config = load_config()
    return jsonify({'schedules': config.get('wol_schedules', [])})


@app.route('/api/wol/schedules', methods=['POST'])
@login_required
@require_operator
def api_wol_schedules_create():
    """Create a new WoL schedule."""
    data = request.get_json() or {}
    target_name = str(data.get('target_name', '')).strip()
    time_hhmm = str(data.get('time_hhmm', '')).strip()
    days = data.get('days', list(range(7)))  # default all days
    enabled = bool(data.get('enabled', True))

    if not target_name:
        return jsonify({'error': 'target_name required'}), 400
    if not re.match(r'^([01]\d|2[0-3]):[0-5]\d$', time_hhmm):
        return jsonify({'error': 'time_hhmm must be HH:MM (24-hour)'}), 400
    if not isinstance(days, list) or not all(isinstance(d, int) and 0 <= d <= 6 for d in days):
        return jsonify({'error': 'days must be a list of ints 0-6 (Mon-Sun)'}), 400

    schedule = {
        'id': secrets.token_hex(8),
        'target_name': target_name,
        'time_hhmm': time_hhmm,
        'days': days,
        'enabled': enabled,
        'created_at': datetime.utcnow().isoformat(),
        'created_by': current_user.username,
    }
    config = load_config()
    config.setdefault('wol_schedules', []).append(schedule)
    save_config(config)
    logger.info(f"WoL schedule created by {current_user.username}: {target_name} at {time_hhmm}")
    return jsonify({'status': 'ok', 'schedule': schedule}), 201


@app.route('/api/wol/schedules/<schedule_id>', methods=['PUT'])
@login_required
@require_operator
def api_wol_schedules_update(schedule_id):
    """Enable/disable or update a WoL schedule."""
    config = load_config()
    schedules = config.get('wol_schedules', [])
    target = next((s for s in schedules if s['id'] == schedule_id), None)
    if not target:
        return jsonify({'error': 'Schedule not found'}), 404

    data = request.get_json() or {}
    if 'enabled' in data:
        target['enabled'] = bool(data['enabled'])
    if 'time_hhmm' in data:
        t = str(data['time_hhmm']).strip()
        if not re.match(r'^([01]\d|2[0-3]):[0-5]\d$', t):
            return jsonify({'error': 'time_hhmm must be HH:MM (24-hour)'}), 400
        target['time_hhmm'] = t
    if 'days' in data:
        days = data['days']
        if not isinstance(days, list) or not all(isinstance(d, int) and 0 <= d <= 6 for d in days):
            return jsonify({'error': 'days must be a list of ints 0-6'}), 400
        target['days'] = days

    save_config(config)
    return jsonify({'status': 'ok', 'schedule': target})


@app.route('/api/wol/schedules/<schedule_id>', methods=['DELETE'])
@login_required
@require_operator
def api_wol_schedules_delete(schedule_id):
    """Delete a WoL schedule."""
    config = load_config()
    schedules = config.get('wol_schedules', [])
    updated = [s for s in schedules if s['id'] != schedule_id]
    if len(updated) == len(schedules):
        return jsonify({'error': 'Schedule not found'}), 404
    config['wol_schedules'] = updated
    save_config(config)
    logger.info(f"WoL schedule {schedule_id} deleted by {current_user.username}")
    return jsonify({'status': 'ok'})


@app.route('/health', methods=['GET'])
def health_check():
    """Unauthenticated health check endpoint."""
    stats = system_monitor.get_all() if system_monitor else {}
    cert_info = None
    if cert_manager and cert_manager.has_certificate():
        info = cert_manager.get_cert_info()
        cert_info = {'expiry': info.get('not_after'), 'self_signed': info.get('self_signed')}
    
    # Check if critical devices are available
    hid_ok = hid_controller.connected if hid_controller else False
    video_ok = video_streamer.is_running() if video_streamer else False
    critical_devices_ok = hid_ok and video_ok
    
    response = {
        'status': 'ok' if critical_devices_ok else 'degraded',
        'hid_connected': hid_ok,
        'video_running': video_ok,
        'cpu_percent': stats.get('cpu', {}).get('percent'),
        'memory_percent': stats.get('memory', {}).get('percent'),
        'disk_percent': stats.get('disk', {}).get('percent'),
        'certificate': cert_info,
        'timestamp': datetime.utcnow().isoformat(),
    }
    
    # Return 503 Service Unavailable if critical devices are offline
    if not critical_devices_ok:
        return jsonify(response), 503
    
    return jsonify(response)


@app.route('/healthz', methods=['GET'])
def healthz():
    """Kubernetes-style liveness probe alias for /health."""
    return health_check()


@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found'}), 404


@app.errorhandler(500)
def server_error(error):
    logger.error(f"Server error: {error}")
    return jsonify({'error': 'Internal server error'}), 500


def graceful_shutdown(signum, frame):
    """Handle graceful shutdown."""
    logger.info("Shutting down gracefully...")
    if session_recorder and session_recorder.is_recording:
        if video_streamer:
            video_streamer.set_record_callback(None)
        session_recorder.stop_recording()
    if pxe_server:
        pxe_server.stop()
    if hid_controller:
        hid_controller.disconnect()
    if video_streamer:
        video_streamer.stop()
    logger.info("Shutdown complete")
    raise SystemExit(0)


def _wol_scheduler_loop():
    """Background thread: fire WoL schedules at their configured time."""
    import time as _time
    _last_minute = -1
    while True:
        _time.sleep(15)
        try:
            now = datetime.utcnow()
            # Convert UTC to a simple HH:MM string (at-the-minute granularity)
            # Use local time for scheduling UX
            import time as _ltime
            lt = _ltime.localtime()
            current_hhmm = f"{lt.tm_hour:02d}:{lt.tm_min:02d}"
            current_minute = lt.tm_hour * 60 + lt.tm_min
            if current_minute == _last_minute:
                continue
            _last_minute = current_minute
            current_weekday = lt.tm_wday  # 0=Mon, 6=Sun

            config = load_config()
            for sched in config.get('wol_schedules', []):
                if not sched.get('enabled', True):
                    continue
                if sched.get('time_hhmm') != current_hhmm:
                    continue
                if current_weekday not in sched.get('days', list(range(7))):
                    continue
                target = sched.get('target_name', '')
                if not target:
                    continue
                logger.info(f"WoL scheduler firing: {target} at {current_hhmm}")
                result = wol_manager.send_wol(target)
                if isinstance(result, dict) and result.get('status') == 'ok':
                    audit_log.log('wol_schedule_fire', username='scheduler',
                                  ip_address='127.0.0.1', details={'target': target, 'time': current_hhmm})
                else:
                    logger.warning(f"WoL scheduled send failed for {target}: {result}")
        except Exception as e:
            logger.error(f"WoL scheduler error: {e}")


def _le_renewal_loop():
    """Background thread: check Let's Encrypt cert expiry daily and auto-renew."""
    import time as _time
    _time.sleep(3600)  # Initial delay before first check
    while True:
        try:
            if cert_manager and cert_manager.has_certificate():
                days_left = cert_manager.check_expiry_days()
                if days_left is not None and days_left <= 30:
                    logger.info(f"Certificate expires in {days_left} days — attempting auto-renewal")
                    try:
                        result = cert_manager.auto_renew()
                        if result is True:
                            logger.info("Certificate auto-renewed successfully")
                        else:
                            logger.warning(f"Certificate auto-renewal failed: {result}")
                    except Exception as e:
                        logger.error(f"Auto-renewal exception: {e}")
        except Exception as e:
            logger.error(f"LE renewal check error: {e}")
        _time.sleep(86400)  # Check once per day


# Initialize subsystems (runs when module is loaded, works with both direct execution and gunicorn)
signal.signal(signal.SIGTERM, graceful_shutdown)
signal.signal(signal.SIGINT, graceful_shutdown)

init_hid()
init_video()
init_pxe()
init_recorder()
init_cert_manager()
init_edid()
init_macros()
init_notifications()
init_oidc()
init_backup()
setup_remote_syslog()

# Migrate plaintext config files to encrypted storage (if config.key exists)
if config_crypto.enabled:
    for _path in (USERS_PATH, Path(TOTP_PATH)):
        if config_crypto.migrate_plaintext(_path):
            logger.info(f"Migrated {_path} to encrypted storage")

# Ensure all users have password change flag set (migration for existing users)
ensure_password_change_flag()

# Start WoL scheduler background thread
threading.Thread(target=_wol_scheduler_loop, daemon=True, name='wol-scheduler').start()
logger.info("WoL scheduler started")

# Start Let's Encrypt auto-renewal background thread
threading.Thread(target=_le_renewal_loop, daemon=True, name='le-renewal').start()
logger.info("LE auto-renewal thread started")


if __name__ == '__main__':
    
    use_https = os.path.exists('cert.pem') and os.path.exists('key.pem')
    ssl_ctx = ('cert.pem', 'key.pem') if use_https else None
    
    if use_https:
        app.config['SESSION_COOKIE_SECURE'] = True
        logger.info("Starting with HTTPS")
    else:
        app.config['SESSION_COOKIE_SECURE'] = False
        logger.info("Starting with HTTP (SESSION_COOKIE_SECURE disabled)")
    
    app.run(
        host='0.0.0.0',
        port=5000,
        debug=False,
        threaded=True,
        ssl_context=ssl_ctx
    )
