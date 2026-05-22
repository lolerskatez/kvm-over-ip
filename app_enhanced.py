import os
import json
import logging
from functools import wraps
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash

from hid_controller import CH9329HIDController
from video_streamer import VideoStreamer
from totp_manager import TOTPManager
from session_manager import SessionManager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = 28800

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

CONFIG_PATH = Path('/etc/kvm/config.json') if os.path.exists('/etc/kvm') else Path('./config.json')
USERS_PATH = Path('/etc/kvm/users.json') if os.path.exists('/etc/kvm') else Path('./users.json')
SETTINGS_PATH = Path('/etc/kvm/settings.json') if os.path.exists('/etc/kvm') else Path('./settings.json')

hid_controller = None
video_streamer = None
totp_manager = None
session_manager = None


class User(UserMixin):
    """User model for Flask-Login."""
    def __init__(self, username):
        self.id = username
        self.username = username


@login_manager.user_loader
def load_user(username):
    return User(username)


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
    """Load users from JSON file."""
    try:
        if USERS_PATH.exists():
            with open(USERS_PATH, 'r') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load users: {e}")
    
    return {}


def save_users(users):
    """Save users to JSON file."""
    try:
        USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(USERS_PATH, 'w') as f:
            json.dump(users, f, indent=2)
        USERS_PATH.chmod(0o640)
        return True
    except Exception as e:
        logger.error(f"Failed to save users: {e}")
        return False


def load_settings():
    """Load system settings."""
    try:
        if SETTINGS_PATH.exists():
            with open(SETTINGS_PATH, 'r') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load settings: {e}")
    
    return {
        '2fa_enabled_globally': False,
        'require_2fa_for_all': False,
        'session_timeout_minutes': 480,
        'max_failed_attempts': 5,
        'failed_attempt_window_minutes': 15
    }


def save_settings(settings):
    """Save system settings."""
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(SETTINGS_PATH, 'w') as f:
            json.dump(settings, f, indent=2)
        SETTINGS_PATH.chmod(0o640)
        return True
    except Exception as e:
        logger.error(f"Failed to save settings: {e}")
        return False


def get_client_ip():
    """Get client IP address."""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0]
    return request.remote_addr


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


def init_managers():
    """Initialize TOTP and session managers."""
    global totp_manager, session_manager
    totp_manager = TOTPManager()
    session_manager = SessionManager()
    logger.info("TOTP and session managers initialized")


@app.route('/')
def index():
    """Redirect to login or console."""
    if current_user.is_authenticated:
        return redirect(url_for('console'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page and authentication."""
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        
        if not username or not password:
            return render_template('login.html', error='Username and password required'), 400
        
        users = load_users()
        user_data = users.get(username)
        
        client_ip = get_client_ip()
        
        if not user_data or not check_password_hash(user_data['password'], password):
            session_manager.record_login_attempt(username, False, client_ip)
            
            if session_manager.is_brute_force_attempt(username):
                logger.warning(f"Brute force attempt detected for user {username} from {client_ip}")
                return render_template('login.html', error='Too many failed attempts. Please try again later.'), 429
            
            logger.warning(f"Failed login attempt for user {username} from {client_ip}")
            return render_template('login.html', error='Invalid credentials'), 401
        
        active_user = session_manager.get_active_session()
        if active_user and active_user != username:
            session_manager.add_alert(
                'login_attempt',
                active_user,
                f'User {username} is attempting to log in',
                username
            )
            return render_template('login.html', error='Another user is currently logged in. Please try again later.'), 403
        
        settings = load_settings()
        if totp_manager.is_2fa_enabled(username) or settings.get('require_2fa_for_all'):
            session['pre_2fa_username'] = username
            session['pre_2fa_ip'] = client_ip
            return redirect(url_for('verify_2fa'))
        
        user = User(username)
        login_user(user)
        session_manager.create_session(username, session.sid, client_ip, request.headers.get('User-Agent', ''))
        session_manager.record_login_attempt(username, True, client_ip)
        logger.info(f"User {username} logged in from {client_ip}")
        
        return redirect(url_for('console'))
    
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
        
        if token:
            if totp_manager.verify_token(username, token):
                user = User(username)
                login_user(user)
                client_ip = session.get('pre_2fa_ip', get_client_ip())
                session_manager.create_session(username, session.sid, client_ip, request.headers.get('User-Agent', ''))
                session_manager.record_login_attempt(username, True, client_ip)
                session.pop('pre_2fa_username', None)
                session.pop('pre_2fa_ip', None)
                logger.info(f"User {username} passed 2FA verification from {client_ip}")
                return redirect(url_for('console'))
            else:
                return render_template('verify_2fa.html', error='Invalid 2FA code'), 401
        
        elif backup_code:
            if totp_manager.verify_backup_code(username, backup_code):
                user = User(username)
                login_user(user)
                client_ip = session.get('pre_2fa_ip', get_client_ip())
                session_manager.create_session(username, session.sid, client_ip, request.headers.get('User-Agent', ''))
                session_manager.record_login_attempt(username, True, client_ip)
                session.pop('pre_2fa_username', None)
                session.pop('pre_2fa_ip', None)
                logger.warning(f"User {username} used backup code for 2FA from {client_ip}")
                return redirect(url_for('console'))
            else:
                return render_template('verify_2fa.html', error='Invalid backup code'), 401
        
        return render_template('verify_2fa.html', error='Please enter a 2FA code or backup code'), 400
    
    return render_template('verify_2fa.html')


@app.route('/logout')
@login_required
def logout():
    """Logout user."""
    username = current_user.username
    session_manager.end_session(username)
    logout_user()
    logger.info(f"User {username} logged out")
    return redirect(url_for('login'))


@app.route('/console')
@login_required
def console():
    """Main KVM console interface."""
    config = load_config()
    session_manager.update_activity(current_user.username)
    
    active_user = session_manager.get_active_session()
    if active_user != current_user.username:
        logout_user()
        return redirect(url_for('login'))
    
    return render_template('console.html', config=config)


@app.route('/stream')
@login_required
def stream():
    """Video stream endpoint (MJPEG)."""
    session_manager.update_activity(current_user.username)
    
    if not video_streamer or not video_streamer.is_running():
        return "Video stream not available", 503
    
    return video_streamer.get_stream_response()


@app.route('/api/keyboard', methods=['POST'])
@login_required
def api_keyboard():
    """Handle keyboard input."""
    session_manager.update_activity(current_user.username)
    
    if not hid_controller or not hid_controller.connected:
        return jsonify({'error': 'HID device not available'}), 503
    
    data = request.get_json()
    action = data.get('action')
    
    try:
        if action == 'key':
            keycode = data.get('keycode')
            pressed = data.get('pressed', True)
            hid_controller.send_key(keycode, pressed)
        
        elif action == 'key_with_modifier':
            keycode = data.get('keycode')
            modifiers = data.get('modifiers', 0)
            hid_controller.send_key_with_modifier(keycode, modifiers)
        
        elif action == 'text':
            text = data.get('text', '')
            hid_controller.send_text(text)
        
        elif action == 'ctrl_alt_del':
            hid_controller.send_ctrl_alt_del()
        
        else:
            return jsonify({'error': 'Unknown action'}), 400
        
        return jsonify({'status': 'ok'})
    
    except Exception as e:
        logger.error(f"Keyboard error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/mouse', methods=['POST'])
@login_required
def api_mouse():
    """Handle mouse input."""
    session_manager.update_activity(current_user.username)
    
    if not hid_controller or not hid_controller.connected:
        return jsonify({'error': 'HID device not available'}), 503
    
    data = request.get_json()
    action = data.get('action')
    
    try:
        if action == 'move':
            x = data.get('x', 0)
            y = data.get('y', 0)
            wheel = data.get('wheel', 0)
            hid_controller.send_mouse_move(x, y, wheel)
        
        elif action == 'click':
            button = data.get('button', 'left')
            pressed = data.get('pressed', True)
            hid_controller.send_mouse_click(button, pressed)
        
        else:
            return jsonify({'error': 'Unknown action'}), 400
        
        return jsonify({'status': 'ok'})
    
    except Exception as e:
        logger.error(f"Mouse error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/config', methods=['GET', 'POST'])
@login_required
def api_config():
    """Get or update configuration."""
    session_manager.update_activity(current_user.username)
    
    if request.method == 'GET':
        config = load_config()
        return jsonify(config)
    
    data = request.get_json()
    config = load_config()
    
    allowed_keys = ['video_device', 'hid_device', 'resolution', 'framerate', 'bitrate']
    for key in allowed_keys:
        if key in data:
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
    session_manager.update_activity(current_user.username)
    
    alerts = session_manager.get_unread_alerts(current_user.username)
    
    return jsonify({
        'user': current_user.username,
        'hid_connected': hid_controller.connected if hid_controller else False,
        'video_running': video_streamer.is_running() if video_streamer else False,
        'timestamp': datetime.utcnow().isoformat(),
        'alerts': alerts,
        'alerts_count': len(alerts)
    })


@app.route('/api/alerts', methods=['GET'])
@login_required
def api_alerts():
    """Get user alerts."""
    session_manager.update_activity(current_user.username)
    
    limit = request.args.get('limit', 20, type=int)
    alerts = session_manager.get_all_alerts(current_user.username, limit)
    
    return jsonify({
        'alerts': alerts,
        'count': len(alerts)
    })


@app.route('/api/alerts/mark-read', methods=['POST'])
@login_required
def api_alerts_mark_read():
    """Mark all alerts as read."""
    session_manager.update_activity(current_user.username)
    session_manager.mark_alerts_read(current_user.username)
    return jsonify({'status': 'ok'})


@app.route('/api/2fa/setup', methods=['GET', 'POST'])
@login_required
def api_2fa_setup():
    """Setup 2FA for current user."""
    session_manager.update_activity(current_user.username)
    
    username = current_user.username
    
    if request.method == 'GET':
        secret, provisioning_uri = totp_manager.generate_secret(username)
        qr_code = totp_manager.get_qr_code(username, secret)
        
        return jsonify({
            'secret': secret,
            'qr_code': qr_code,
            'provisioning_uri': provisioning_uri
        })
    
    data = request.get_json()
    secret = data.get('secret')
    token = data.get('token')
    
    if not secret or not token:
        return jsonify({'error': 'Missing secret or token'}), 400
    
    if not totp_manager.verify_token(username, token):
        return jsonify({'error': 'Invalid 2FA token'}), 401
    
    if totp_manager.enable_2fa(username, secret):
        backup_codes = totp_manager.get_backup_codes(username)
        logger.info(f"2FA enabled for user {username}")
        return jsonify({
            'status': 'ok',
            'backup_codes': backup_codes
        })
    else:
        return jsonify({'error': 'Failed to enable 2FA'}), 500


@app.route('/api/2fa/disable', methods=['POST'])
@login_required
def api_2fa_disable():
    """Disable 2FA for current user."""
    session_manager.update_activity(current_user.username)
    
    username = current_user.username
    data = request.get_json()
    password = data.get('password', '')
    
    users = load_users()
    user_data = users.get(username)
    
    if not user_data or not check_password_hash(user_data['password'], password):
        return jsonify({'error': 'Invalid password'}), 401
    
    if totp_manager.disable_2fa(username):
        logger.info(f"2FA disabled for user {username}")
        return jsonify({'status': 'ok'})
    else:
        return jsonify({'error': 'Failed to disable 2FA'}), 500


@app.route('/api/2fa/status', methods=['GET'])
@login_required
def api_2fa_status():
    """Get 2FA status for current user."""
    session_manager.update_activity(current_user.username)
    
    username = current_user.username
    status = totp_manager.get_user_2fa_status(username)
    
    return jsonify(status)


@app.route('/api/settings', methods=['GET', 'POST'])
@login_required
def api_settings():
    """Get or update system settings (admin only)."""
    session_manager.update_activity(current_user.username)
    
    users = load_users()
    if not users.get(current_user.username, {}).get('is_admin', False):
        return jsonify({'error': 'Admin access required'}), 403
    
    if request.method == 'GET':
        settings = load_settings()
        return jsonify(settings)
    
    data = request.get_json()
    settings = load_settings()
    
    allowed_keys = ['2fa_enabled_globally', 'require_2fa_for_all', 'session_timeout_minutes', 
                    'max_failed_attempts', 'failed_attempt_window_minutes']
    
    for key in allowed_keys:
        if key in data:
            settings[key] = data[key]
    
    if save_settings(settings):
        logger.info(f"Settings updated by {current_user.username}")
        return jsonify({'status': 'ok', 'settings': settings})
    else:
        return jsonify({'error': 'Failed to save settings'}), 500


@app.route('/api/users', methods=['GET'])
@login_required
def api_users():
    """Get user list (admin only)."""
    session_manager.update_activity(current_user.username)
    
    users = load_users()
    if not users.get(current_user.username, {}).get('is_admin', False):
        return jsonify({'error': 'Admin access required'}), 403
    
    user_list = []
    for username, user_data in users.items():
        totp_status = totp_manager.get_user_2fa_status(username)
        user_list.append({
            'username': username,
            'is_admin': user_data.get('is_admin', False),
            '2fa_enabled': totp_status['enabled'],
            'backup_codes_remaining': totp_status['backup_codes_remaining']
        })
    
    return jsonify({'users': user_list})


@app.route('/api/session-info', methods=['GET'])
@login_required
def api_session_info():
    """Get current session information."""
    session_manager.update_activity(current_user.username)
    
    session_info = session_manager.get_session_info(current_user.username)
    
    if not session_info:
        return jsonify({'error': 'Session not found'}), 404
    
    return jsonify(session_info)


@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found'}), 404


@app.errorhandler(500)
def server_error(error):
    logger.error(f"Server error: {error}")
    return jsonify({'error': 'Internal server error'}), 500


if __name__ == '__main__':
    init_managers()
    init_hid()
    init_video()
    
    app.run(
        host='0.0.0.0',
        port=5000,
        debug=False,
        threaded=True
    )
