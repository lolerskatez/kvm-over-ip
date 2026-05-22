"""Flask application factory and configuration."""

import os
import secrets
import logging
from pathlib import Path
from flask import Flask, session, jsonify, request, redirect, url_for
from flask_login import LoginManager, UserMixin, current_user, logout_user
from flask_sock import Sock

from app.utils import (
    get_client_ip,
    validate_csrf_token,
    check_ip_acl,
    load_users,
    load_config,
    ensure_password_change_flag,
    generate_csrf_token,
    get_config_paths,
)

logger = logging.getLogger(__name__)


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


def create_app(config=None):
    """Application factory.
    
    Args:
        config: Optional configuration dictionary to override defaults.
        
    Returns:
        Flask: Configured Flask application instance.
    """
    app = Flask(__name__, 
                template_folder='../templates',
                static_folder='../static')
    
    # Configure Flask session
    app.config['SECRET_KEY'] = get_or_generate_secret_key()
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['PERMANENT_SESSION_LIFETIME'] = 28800
    
    if config:
        app.config.update(config)
    
    # Initialize extensions
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    
    @login_manager.user_loader
    def load_user(username):
        """Load user only if they exist in users.json."""
        users = load_users()
        if username in users:
            return User(username)
        return None
    
    sock = Sock(app)
    
    # Register before_request hooks
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
            # Exempt authentication and real-time control endpoints from CSRF
            if request.path in ('/login', '/verify-2fa', '/api/mouse', '/api/keyboard'):
                return
            if not validate_csrf_token():
                return jsonify({'error': 'CSRF token missing or invalid'}), 403
    
    @app.before_request
    def check_inactivity():
        """Auto-logout users who exceed the idle timeout."""
        if not current_user.is_authenticated:
            return
        # Skip for static/non-interactive endpoints
        if request.path in ('/logout', '/login', '/health', '/healthz'):
            return

        from app.services.auth.session_manager import SessionManager
        paths = get_config_paths()
        session_manager = SessionManager(paths['sessions'])
        
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
            return redirect(url_for('auth.login'))

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
            current_role=role,
        )
    
    # Register blueprints
    from app.routes.auth import auth_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.console import console_bp
    from app.routes.hardware import hardware_bp
    from app.routes.system import system_bp
    from app.routes.pxe import pxe_bp
    from app.routes.users import users_bp
    from app.routes.settings import settings_bp
    from app.routes.recordings import recordings_bp
    
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(console_bp)
    app.register_blueprint(hardware_bp)
    app.register_blueprint(system_bp)
    app.register_blueprint(pxe_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(recordings_bp)
    
    # Initialize services in blueprints
    from app.routes.hardware import init_hardware_services
    from app.routes.system import init_system_services
    from app.routes.settings import init_settings_services
    from app.routes.pxe import init_pxe_services
    from app.routes.recordings import init_recordings_services
    
    try:
        # Import services - they may not all initialize successfully but that's OK
        from app.services.hardware.hid_controller import CH9329HIDController
        from app.services.hardware.video_streamer import VideoStreamer
        from app.services.hardware.edid_manager import EDIDManager
        from app.services.system.system_monitor import SystemMonitor
        from app.services.system.cert_manager import CertManager
        from app.services.system.wake_on_lan import WakeOnLANManager
        from app.services.management.macro_manager import MacroManager
        from app.services.management.notification_manager import NotificationManager
        from app.services.management.backup_manager import BackupManager
        from app.services.pxe.pxe_server import PXEServer
        from app.services.audit.audit_log import AuditLog
        from app.services.audit.session_recorder import SessionRecorder
        
        # Initialize services with minimal args
        hid = CH9329HIDController()
        video = VideoStreamer()
        edid = EDIDManager()
        recorder = SessionRecorder()
        audit = AuditLog()
        init_hardware_services(hid=hid, video=video, edid=edid, recorder=recorder, audit=audit)
        
        sys_mon = SystemMonitor()
        cert_mgr = CertManager()
        wol_mgr = WakeOnLANManager()
        init_system_services(sys_mon=sys_mon, cert_mgr=cert_mgr, wol_mgr=wol_mgr, audit=audit, hid=hid, video=video)
        
        macro_mgr = MacroManager()
        notif_mgr = NotificationManager()
        backup_mgr = BackupManager()
        init_settings_services(macros=macro_mgr, notif=notif_mgr, backup=backup_mgr, audit=audit, hid=hid)
        
        pxe = PXEServer()
        init_pxe_services(pxe=pxe)
        
        init_recordings_services(video=video)
        
        logger.info("All service instances initialized successfully")
    except Exception as e:
        logger.warning(f"Service initialization warning (routes still available): {e}")
    
    # Initialize data migration
    ensure_password_change_flag()
    
    return app


def get_config_paths():
    """Get configuration file paths based on environment.
    
    Returns:
        dict: Contains paths for config, users, sessions, totp, and crypto key files.
    """
    from app.utils.config import get_config_paths as _get_config_paths
    return _get_config_paths()


# Export User and other key classes for use by blueprints
__all__ = ['create_app', 'User', 'get_config_paths']


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    app = create_app()
    app.run(debug=True)
