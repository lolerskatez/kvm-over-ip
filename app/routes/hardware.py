"""Hardware control routes blueprint (HID, video, EDID)."""

import logging
import json
import re
import secrets
from datetime import datetime
from flask import Blueprint, render_template, jsonify, request, Response
from flask_login import login_required, current_user
from flask_sock import Sock

from app.utils import require_kvm_access, require_admin, get_client_ip, load_config, save_config, load_users

logger = logging.getLogger(__name__)

hardware_bp = Blueprint('hardware', __name__)
sock = Sock()

# Service instances - initialized by app factory
hid_controller = None
video_streamer = None
edid_manager = None
session_recorder = None
audit_log = None


def init_hardware_services(hid=None, video=None, edid=None, recorder=None, audit=None):
    """Initialize hardware services."""
    global hid_controller, video_streamer, edid_manager, session_recorder, audit_log
    hid_controller = hid
    video_streamer = video
    edid_manager = edid
    session_recorder = recorder
    audit_log = audit
    sock.init_app(None)  # Will be properly initialized in app factory


@hardware_bp.route('/console')
@login_required
def console():
    """Console interface (redirect)."""
    return render_template('console.html')


@hardware_bp.route('/api/keyboard', methods=['POST'])
@login_required
@require_kvm_access
def api_keyboard():
    """Send keyboard input to HID device."""
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
            if audit_log:
                audit_log.log_keyboard_input(current_user.username, client_ip, keycode=keycode)
        
        elif action == 'key_with_modifier':
            keycode = data.get('keycode')
            modifiers = data.get('modifiers', 0)
            hid_controller.send_key_with_modifier(keycode, modifiers)
            if audit_log:
                audit_log.log_keyboard_input(current_user.username, client_ip, keycode=keycode, modifiers=modifiers)
        
        elif action == 'text':
            text = data.get('text', '')
            hid_controller.send_text(text)
            if audit_log:
                audit_log.log_keyboard_input(current_user.username, client_ip, text=text)
        
        elif action == 'ctrl_alt_del':
            hid_controller.send_ctrl_alt_del()
            if audit_log:
                audit_log.log_keyboard_input(current_user.username, client_ip, keycode='ctrl_alt_del')
        
        else:
            return jsonify({'error': 'Unknown action'}), 400
        
        return jsonify({'status': 'ok'})
    
    except Exception as e:
        logger.error(f"Keyboard error: {e}")
        return jsonify({'error': 'Keyboard command failed'}), 500


@hardware_bp.route('/api/mouse', methods=['POST'])
@login_required
@require_kvm_access
def api_mouse():
    """Send mouse input to HID device."""
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
            if data.get('_log_movement') and audit_log:
                audit_log.log_mouse_movement(current_user.username, client_ip, mode='relative')
        
        elif action == 'move_abs':
            x = data.get('x', 0)
            y = data.get('y', 0)
            wheel = data.get('wheel', 0)
            hid_controller.send_mouse_move_absolute(x, y, wheel)
            if data.get('_log_movement') and audit_log:
                audit_log.log_mouse_movement(current_user.username, client_ip, mode='absolute')
        
        elif action == 'click':
            button = data.get('button', 'left')
            pressed = data.get('pressed', True)
            hid_controller.send_mouse_click(button, pressed)
            if audit_log:
                audit_log.log_mouse_click(current_user.username, client_ip, button)
        
        else:
            return jsonify({'error': 'Unknown action'}), 400
        
        return jsonify({'status': 'ok'})
    
    except Exception as e:
        logger.error(f"Mouse error: {e}")
        return jsonify({'error': 'Mouse command failed'}), 500


@hardware_bp.route('/api/mouse/mode', methods=['GET', 'POST'])
@login_required
@require_kvm_access
def api_mouse_mode():
    """Get or set mouse input mode (absolute/relative)."""
    if request.method == 'GET':
        return jsonify({'mode': 'absolute'})
    
    data = request.get_json()
    mode = data.get('mode', '')
    if mode not in ('absolute', 'relative'):
        return jsonify({'error': 'Invalid mode. Use "absolute" or "relative"'}), 400
    return jsonify({'status': 'ok', 'mode': mode})


@hardware_bp.route('/api/screenshot', methods=['GET'])
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


@hardware_bp.route('/api/video/stats', methods=['GET'])
@login_required
def api_video_stats():
    """Get current video stream statistics (FPS, bandwidth, resolution)."""
    if not video_streamer or not video_streamer.running:
        return jsonify({'error': 'Video not available'}), 503
    return jsonify(video_streamer.get_stream_stats())


@hardware_bp.route('/api/video/quality', methods=['POST'])
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

    logger.info(f"Video quality updated by {current_user.username}: res={resolution} fps={framerate} bitrate={bitrate}")
    return jsonify({'status': 'ok', 'success': success})


@hardware_bp.route('/api/edid/status', methods=['GET'])
@login_required
def api_edid_status():
    """Get EDID emulation status and available presets."""
    if not edid_manager:
        return jsonify({'error': 'EDID manager not configured'}), 503
    return jsonify(edid_manager.get_status())


@hardware_bp.route('/api/edid/preset', methods=['POST'])
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


@hardware_bp.route('/api/edid/upload', methods=['POST'])
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


@hardware_bp.route('/api/edid/clear', methods=['POST'])
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
