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
webrtc_signaling = None


def init_hardware_services(hid=None, video=None, edid=None, recorder=None, audit=None, webrtc=None):
    """Initialize hardware services."""
    global hid_controller, video_streamer, edid_manager, session_recorder, audit_log, webrtc_signaling
    hid_controller = hid
    video_streamer = video
    edid_manager = edid
    session_recorder = recorder
    audit_log = audit
    webrtc_signaling = webrtc
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


# ═══════════════════════════════════════════════════════════
# WebRTC Routes
# ═══════════════════════════════════════════════════════════

@hardware_bp.route('/api/webrtc/offer', methods=['POST'])
@login_required
@require_kvm_access
def api_webrtc_offer():
    """Handle WebRTC SDP offer from client and return answer."""
    if not webrtc_signaling:
        return jsonify({'error': 'WebRTC service not available'}), 503
    
    data = request.get_json()
    if not data or 'sdp' not in data or 'type' not in data:
        return jsonify({'error': 'Missing SDP offer'}), 400
    
    client_ip = get_client_ip()
    
    try:
        # Create peer connection and handle offer
        result = webrtc_signaling.handle_offer(
            username=current_user.username,
            client_ip=client_ip,
            sdp=data['sdp'],
            sdp_type=data['type']
        )
        
        if 'error' in result:
            return jsonify(result), 400
        
        # Log WebRTC session start
        if audit_log:
            audit_log.log_action(
                username=current_user.username,
                action='webrtc_session_start',
                ip=client_ip,
                details={'session_id': result.get('session_id')}
            )
        
        logger.info(f"WebRTC session started for {current_user.username} from {client_ip}")
        
        return jsonify(result)
    
    except Exception as e:
        logger.error(f"WebRTC offer handling failed: {e}", exc_info=True)
        return jsonify({'error': 'Failed to process offer'}), 500


@hardware_bp.route('/api/webrtc/ice-candidate', methods=['POST'])
@login_required
@require_kvm_access
def api_webrtc_ice_candidate():
    """Handle ICE candidate from client."""
    if not webrtc_signaling:
        return jsonify({'error': 'WebRTC service not available'}), 503
    
    data = request.get_json()
    if not data or 'session_id' not in data or 'candidate' not in data:
        return jsonify({'error': 'Missing session_id or candidate'}), 400
    
    try:
        result = webrtc_signaling.handle_ice_candidate(
            session_id=data['session_id'],
            candidate=data['candidate'],
            sdp_mid=data.get('sdpMid'),
            sdp_mline_index=data.get('sdpMLineIndex')
        )
        
        if 'error' in result:
            return jsonify(result), 400
        
        return jsonify({'status': 'ok'})
    
    except Exception as e:
        logger.error(f"ICE candidate handling failed: {e}", exc_info=True)
        return jsonify({'error': 'Failed to process ICE candidate'}), 500


@hardware_bp.route('/api/webrtc/stats', methods=['GET'])
@login_required
def api_webrtc_stats():
    """Get WebRTC session statistics."""
    if not webrtc_signaling:
        return jsonify({'error': 'WebRTC service not available'}), 503
    
    session_id = request.args.get('session_id')
    if not session_id:
        return jsonify({'error': 'Missing session_id parameter'}), 400
    
    try:
        # Get peer info for this session
        peer_info = webrtc_signaling.get_peer_info(session_id)
        
        if not peer_info:
            return jsonify({'error': 'Session not found'}), 404
        
        # Build stats response
        stats = {
            'session_id': session_id,
            'state': peer_info.get('state'),
            'username': peer_info.get('username'),
            'created_at': peer_info.get('created_at').isoformat() if peer_info.get('created_at') else None,
            'encoder': {
                'name': 'H.264',  # TODO: Get from video track
                'type': 'hardware'  # TODO: Get from encoder detector
            },
            'bitrate_kbps': 5000,  # TODO: Get actual bitrate from video track
            'resolution': {
                'width': 1920,  # TODO: Get from config or video track
                'height': 1080
            },
            'fps': 30  # TODO: Get from config or video track
        }
        
        return jsonify(stats)
    
    except Exception as e:
        logger.error(f"Failed to get WebRTC stats: {e}", exc_info=True)
        return jsonify({'error': 'Failed to retrieve stats'}), 500


@hardware_bp.route('/api/webrtc/close', methods=['POST'])
@login_required
def api_webrtc_close():
    """Close a WebRTC peer connection."""
    if not webrtc_signaling:
        return jsonify({'error': 'WebRTC service not available'}), 503
    
    data = request.get_json()
    session_id = data.get('session_id') if data else None
    
    if not session_id:
        return jsonify({'error': 'Missing session_id'}), 400
    
    try:
        result = webrtc_signaling.close_peer_connection(session_id)
        
        # Log session end
        if audit_log:
            audit_log.log_action(
                username=current_user.username,
                action='webrtc_session_end',
                ip=get_client_ip(),
                details={'session_id': session_id}
            )
        
        logger.info(f"WebRTC session {session_id} closed by {current_user.username}")
        
        return jsonify({'status': 'ok'})
    
    except Exception as e:
        logger.error(f"Failed to close WebRTC session: {e}", exc_info=True)
        return jsonify({'error': 'Failed to close session'}), 500
