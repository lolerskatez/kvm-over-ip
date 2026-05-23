"""System management routes blueprint (monitoring, certificates, WoL, health)."""

import logging
import re
import secrets
from datetime import datetime
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user

from app.utils import (require_admin, require_operator, load_config, save_config, 
                       get_client_ip, load_users, validate_config_value, generate_csrf_token)

logger = logging.getLogger(__name__)

system_bp = Blueprint('system', __name__)

# Service instances
system_monitor = None
cert_manager = None
wol_manager = None
audit_log = None
hid_controller = None
video_streamer = None


def init_system_services(sys_mon=None, cert_mgr=None, wol_mgr=None, audit=None, hid=None, video=None):
    """Initialize system services."""
    global system_monitor, cert_manager, wol_manager, audit_log, hid_controller, video_streamer
    system_monitor = sys_mon
    cert_manager = cert_mgr
    wol_manager = wol_mgr
    audit_log = audit
    hid_controller = hid
    video_streamer = video


@system_bp.route('/api/system/stats', methods=['GET'])
@login_required
def api_system_stats():
    """Get system resource stats (CPU, RAM, disk, temp, network)."""
    if system_monitor is None:
        from app.services.system.system_monitor import SystemMonitor
        sm = SystemMonitor()
    else:
        sm = system_monitor
    return jsonify(sm.get_all() if sm else {'error': 'System monitor unavailable'})


@system_bp.route('/api/ping', methods=['GET'])
@login_required
def api_ping():
    """Lightweight ping for client-side latency measurement."""
    return jsonify({'pong': True, 'ts': datetime.utcnow().isoformat()})


@system_bp.route('/api/status', methods=['GET'])
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
        'idle_timeout': config.get('idle_timeout', 900),
        'stream_stats': video_streamer.get_stream_stats() if video_streamer and video_streamer.is_running() else None,
        'csrf_token': generate_csrf_token(),
        'timestamp': datetime.utcnow().isoformat()
    })


@system_bp.route('/api/reconnect', methods=['POST'])
@login_required
@require_admin
def api_reconnect():
    """Reconnect HID and/or video devices."""
    data = request.get_json() or {}
    results = {}
    
    if data.get('hid', False) and hid_controller:
        try:
            hid_controller.disconnect()
            results['hid'] = 'reconnected'
        except Exception as e:
            logger.error(f"HID reconnect failed: {e}")
            results['hid'] = 'error'
    
    if data.get('video', False) and video_streamer:
        try:
            video_streamer.stop()
            results['video'] = 'reconnected'
        except Exception as e:
            logger.error(f"Video reconnect failed: {e}")
            results['video'] = 'error'
    
    return jsonify({'status': 'ok', 'results': results})


@system_bp.route('/api/config', methods=['GET', 'POST'])
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


@system_bp.route('/api/cert/info', methods=['GET'])
@login_required
@require_admin
def api_cert_info():
    """Get current SSL certificate details."""
    if not cert_manager:
        return jsonify({'error': 'Cert manager not configured'}), 503
    info = cert_manager.get_cert_info()
    return jsonify({'has_certificate': cert_manager.has_certificate(), 'info': info})


@system_bp.route('/api/cert/generate', methods=['POST'])
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


@system_bp.route('/api/cert/upload', methods=['POST'])
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


@system_bp.route('/api/cert/letsencrypt', methods=['POST'])
@login_required
@require_admin
def api_cert_letsencrypt():
    """Request a Let's Encrypt certificate."""
    if not cert_manager:
        return jsonify({'error': 'Cert manager not configured'}), 503
    data = request.get_json() or {}
    domain = data.get('domain', '')
    if not domain:
        return jsonify({'error': 'Domain is required'}), 400
    result = cert_manager.request_letsencrypt(domain, email=data.get('email', '') or None)
    if result is True:
        logger.info(f"Let's Encrypt cert obtained by {current_user.username}: {domain}")
        return jsonify({'status': 'ok', 'info': cert_manager.get_cert_info()})
    return jsonify({'error': result}), 500


@system_bp.route('/api/cert/delete', methods=['POST'])
@login_required
@require_admin
def api_cert_delete():
    """Delete the current certificate (revert to HTTP)."""
    if not cert_manager:
        return jsonify({'error': 'Cert manager not configured'}), 503
    cert_manager.delete_certificate()
    logger.info(f"Certificate deleted by {current_user.username}")
    return jsonify({'status': 'ok', 'message': 'Certificate removed. Restart server to use HTTP.'})


@system_bp.route('/api/cert/renew', methods=['POST'])
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


@system_bp.route('/api/wol/targets', methods=['GET'])
@login_required
def api_wol_targets():
    """List all WOL targets."""
    if not wol_manager:
        return jsonify({'error': 'WOL not configured'}), 503
    return jsonify({'targets': wol_manager.list_targets()})


@system_bp.route('/api/wol/add', methods=['POST'])
@login_required
@require_admin
def api_wol_add_target():
    """Add a WOL target device (admin only)."""
    if not wol_manager:
        return jsonify({'error': 'WOL not configured'}), 503
    data = request.get_json()
    name = data.get('name', '').strip()
    mac_address = data.get('mac_address', '').strip()
    if not name or not mac_address:
        return jsonify({'error': 'Name and MAC address required'}), 400
    try:
        result = wol_manager.add_target(name, mac_address, data.get('broadcast_ip', '255.255.255.255'), data.get('port', 9))
        if result is True:
            logger.info(f"WOL target added by {current_user.username}: {name}")
            return jsonify({'status': 'ok', 'name': name}), 201
        return jsonify({'error': result}), 400
    except Exception as e:
        logger.error(f"WOL add failed: {e}")
        return jsonify({'error': 'Failed to add WOL target'}), 500


@system_bp.route('/api/wol/send', methods=['POST'])
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
            logger.info(f"WOL packet sent by {current_user.username} to {target_name}")
            return jsonify({'status': 'ok', 'sent': target_name})
        return jsonify({'error': result}), 400
    except Exception as e:
        logger.error(f"WOL send failed: {e}")
        return jsonify({'error': 'Failed to send WOL packet'}), 500


@system_bp.route('/api/wol/send-by-mac', methods=['POST'])
@login_required
@require_admin
def api_wol_send_by_mac():
    """Send WOL magic packet by MAC address (admin only)."""
    if not wol_manager:
        return jsonify({'error': 'WOL not configured'}), 503
    data = request.get_json()
    mac_address = data.get('mac_address', '').strip()
    if not mac_address:
        return jsonify({'error': 'MAC address required'}), 400
    try:
        result = wol_manager.send_wol_by_mac(mac_address, data.get('broadcast_ip', '255.255.255.255'), data.get('port', 9))
        if result is True:
            logger.info(f"WOL packet sent by {current_user.username} to {mac_address}")
            return jsonify({'status': 'ok', 'sent': mac_address})
        return jsonify({'error': result}), 400
    except Exception as e:
        logger.error(f"WOL send by MAC failed: {e}")
        return jsonify({'error': 'Failed to send WOL packet'}), 500


@system_bp.route('/api/wol/remove/<target_name>', methods=['DELETE'])
@login_required
@require_admin
def api_wol_remove_target(target_name):
    """Remove a WOL target (admin only)."""
    if not wol_manager:
        return jsonify({'error': 'WOL not configured'}), 503
    try:
        if wol_manager.targets.pop(target_name, None):
            logger.info(f"WOL target removed by {current_user.username}: {target_name}")
            return jsonify({'status': 'ok'})
        return jsonify({'error': 'Target not found'}), 404
    except Exception as e:
        logger.error(f"WOL remove failed: {e}")
        return jsonify({'error': 'Failed to remove WOL target'}), 500


@system_bp.route('/api/wol/schedules', methods=['GET'])
@login_required
@require_operator
def api_wol_schedules_list():
    """List all WoL schedules."""
    config = load_config()
    return jsonify({'schedules': config.get('wol_schedules', [])})


@system_bp.route('/api/wol/schedules', methods=['POST'])
@login_required
@require_operator
def api_wol_schedules_create():
    """Create a new WoL schedule."""
    data = request.get_json() or {}
    target_name = str(data.get('target_name', '')).strip()
    time_hhmm = str(data.get('time_hhmm', '')).strip()
    days = data.get('days', list(range(7)))
    enabled = bool(data.get('enabled', True))
    if not target_name or not re.match(r'^([01]\d|2[0-3]):[0-5]\d$', time_hhmm):
        return jsonify({'error': 'Invalid target_name or time_hhmm'}), 400
    if not isinstance(days, list) or not all(isinstance(d, int) and 0 <= d <= 6 for d in days):
        return jsonify({'error': 'days must be list of ints 0-6'}), 400
    schedule = {'id': secrets.token_hex(8), 'target_name': target_name, 'time_hhmm': time_hhmm, 
                'days': days, 'enabled': enabled, 'created_at': datetime.utcnow().isoformat(), 'created_by': current_user.username}
    config = load_config()
    config.setdefault('wol_schedules', []).append(schedule)
    save_config(config)
    logger.info(f"WoL schedule created by {current_user.username}: {target_name}")
    return jsonify({'status': 'ok', 'schedule': schedule}), 201


@system_bp.route('/api/wol/schedules/<schedule_id>', methods=['PUT'])
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
            return jsonify({'error': 'Invalid time_hhmm'}), 400
        target['time_hhmm'] = t
    if 'days' in data:
        if not isinstance(data['days'], list) or not all(isinstance(d, int) and 0 <= d <= 6 for d in data['days']):
            return jsonify({'error': 'Invalid days'}), 400
        target['days'] = data['days']
    save_config(config)
    return jsonify({'status': 'ok', 'schedule': target})


@system_bp.route('/api/wol/schedules/<schedule_id>', methods=['DELETE'])
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
    logger.info(f"WoL schedule deleted by {current_user.username}: {schedule_id}")
    return jsonify({'status': 'ok'})


@system_bp.route('/health', methods=['GET'])
def health_check():
    """Unauthenticated health check endpoint."""
    from app.services.system.system_monitor import SystemMonitor
    sm = system_monitor if system_monitor else SystemMonitor()
    stats = sm.get_all() if sm else {}
    cert_info = None
    if cert_manager and cert_manager.has_certificate():
        info = cert_manager.get_cert_info()
        cert_info = {'expiry': info.get('not_after'), 'self_signed': info.get('self_signed')}
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
    return jsonify(response) if critical_devices_ok else (jsonify(response), 503)


@system_bp.route('/healthz', methods=['GET'])
def healthz():
    """Kubernetes-style liveness probe alias for /health."""
    return health_check()
