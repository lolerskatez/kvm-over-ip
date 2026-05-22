"""Settings and configuration routes blueprint."""

import logging
import re
import ipaddress
import os
import threading
from datetime import datetime
from io import BytesIO
from flask import Blueprint, render_template, jsonify, request, Response
from flask_login import login_required, current_user

from app.utils import require_admin, load_config, save_config, validate_config_value

logger = logging.getLogger(__name__)

settings_bp = Blueprint('settings', __name__)

# Service instances
macro_manager = None
notification_manager = None
backup_manager = None
audit_log = None
hid_controller = None


def init_settings_services(macros=None, notif=None, backup=None, audit=None, hid=None):
    """Initialize settings services."""
    global macro_manager, notification_manager, backup_manager, audit_log, hid_controller
    macro_manager = macros
    notification_manager = notif
    backup_manager = backup
    audit_log = audit
    hid_controller = hid


@settings_bp.route('/settings')
@login_required
def settings_page():
    """Settings page (admin only)."""
    from app.utils.config import load_users
    
    users = load_users()
    if not users.get(current_user.username, {}).get('is_admin', False):
        return render_template('404.html'), 404
    return render_template('settings.html')


@settings_bp.route('/api/config', methods=['GET', 'POST'])
@login_required
def api_config():
    """Get or update configuration (admin only for POST)."""
    if request.method == 'GET':
        config = load_config()
        return jsonify(config)
    
    from app.utils.config import load_users
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


@settings_bp.route('/api/syslog', methods=['GET', 'POST'])
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
    logger.info(f"Syslog config updated by {current_user.username}")
    return jsonify({'status': 'ok', 'syslog': config['syslog']})


@settings_bp.route('/api/ip-acl', methods=['GET', 'POST'])
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


@settings_bp.route('/api/macros', methods=['GET'])
@login_required
def api_macros_list():
    """List all macros."""
    if not macro_manager:
        return jsonify({'macros': []})
    return jsonify({'macros': macro_manager.list_macros()})


@settings_bp.route('/api/macros/<macro_id>', methods=['GET', 'DELETE'])
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


@settings_bp.route('/api/macros', methods=['POST'])
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


@settings_bp.route('/api/macros/<macro_id>/execute', methods=['POST'])
@login_required
def api_macro_execute(macro_id):
    """Execute a macro."""
    if not macro_manager:
        return jsonify({'error': 'Macros not configured'}), 503
    if not hid_controller or not hid_controller.connected:
        return jsonify({'error': 'HID device not available'}), 503

    def run():
        macro_manager.execute_macro(macro_id, hid_controller)
    threading.Thread(target=run, daemon=True).start()
    logger.info(f"Macro '{macro_id}' executed by {current_user.username}")
    return jsonify({'status': 'ok'})


@settings_bp.route('/api/macros/abort', methods=['POST'])
@login_required
def api_macro_abort():
    """Abort a running macro."""
    if macro_manager:
        macro_manager.abort_macro()
    return jsonify({'status': 'ok'})


@settings_bp.route('/api/notifications/config', methods=['GET', 'POST'])
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


@settings_bp.route('/api/notifications/test/email', methods=['POST'])
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


@settings_bp.route('/api/notifications/test/webhook', methods=['POST'])
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




@settings_bp.route('/api/audit-log', methods=['GET'])
@login_required
@require_admin
def api_audit_log():
    """Get recent audit log entries (admin only)."""
    if not audit_log:
        return jsonify({'entries': []})
    # Get last 100 entries
    entries = audit_log.get_recent_entries(100) if hasattr(audit_log, 'get_recent_entries') else []
    return jsonify({'entries': entries})


@settings_bp.route('/api/backup/export', methods=['POST'])
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

    ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    return Response(
        result,
        mimetype='application/zip',
        headers={'Content-Disposition': f'attachment; filename=kvm_backup_{ts}.zip'}
    )


@settings_bp.route('/api/backup/import', methods=['POST'])
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


@settings_bp.route('/api/backup/files', methods=['GET'])
@login_required
@require_admin
def api_backup_files():
    """List config files that would be included in a backup."""
    if not backup_manager:
        return jsonify({'files': []})
    return jsonify({'files': backup_manager.list_config_files()})


@settings_bp.route('/api/update/check', methods=['GET'])
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


@settings_bp.route('/api/update/apply', methods=['POST'])
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


@settings_bp.route('/api/oidc/config', methods=['GET', 'POST'])
@login_required
@require_admin
def api_oidc_config():
    """Get or update OIDC configuration."""
    config = load_config()

    if request.method == 'GET':
        oc = config.get('oidc', {}).copy()
        if oc.get('client_secret'):
            oc['client_secret'] = '********'
        return jsonify(oc)

    data = request.get_json() or {}
    oc = config.get('oidc', {})
    for k in ('enabled', 'provider_url', 'client_id', 'client_secret', 'scopes', 'auto_create_users'):
        if k in data:
            if k == 'scopes' and isinstance(data[k], list):
                oc[k] = data[k]
            elif k != 'scopes':
                oc[k] = data[k]
    config['oidc'] = oc
    save_config(config)
    logger.info(f"OIDC config updated by {current_user.username}")
    return jsonify({'status': 'ok'})


@settings_bp.route('/api/oidc/test', methods=['POST'])
@login_required
@require_admin
def api_oidc_test():
    """Test OIDC discovery URL connectivity."""
    config = load_config()
    provider_url = config.get('oidc', {}).get('provider_url', '')
    if not provider_url:
        return jsonify({'error': 'Provider URL not configured'}), 400
    
    try:
        import requests as _requests
        resp = _requests.get(f"{provider_url}/.well-known/openid-configuration", timeout=5)
        if resp.status_code == 200:
            return jsonify({'status': 'ok', 'metadata': resp.json()})
        return jsonify({'error': f'HTTP {resp.status_code}'}), resp.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 500
