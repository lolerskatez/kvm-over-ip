"""Recordings routes blueprint."""

import logging
import re
from pathlib import Path
from datetime import datetime
from flask import Blueprint, render_template, jsonify, request, send_file, Response
from flask_login import login_required, current_user

from app.utils import require_admin, require_operator, load_config

logger = logging.getLogger(__name__)

recordings_bp = Blueprint('recordings', __name__)

# Service instances
video_streamer = None


def init_recordings_services(video=None):
    """Initialize recordings services."""
    global video_streamer
    video_streamer = video


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


@recordings_bp.route('/recordings')
@login_required
def recordings_page():
    """Session recording browser and replay page."""
    return render_template('recordings.html')


@recordings_bp.route('/api/recording/start', methods=['POST'])
@login_required
@require_admin
def api_recording_start():
    """Start session recording (admin only)."""
    from app.services.audit.session_recorder import SessionRecorder
    
    config = load_config()
    rec_config = config.get('recording', {})
    recordings_dir = rec_config.get('recordings_dir', '/var/lib/kvm/recordings')
    max_recordings = rec_config.get('max_recordings', 50)
    session_recorder = SessionRecorder(recordings_dir=recordings_dir, max_recordings=max_recordings)
    session_recorder.setup()
    
    rec_id = session_recorder.start_recording(username=current_user.username)
    if rec_id:
        logger.info(f"Recording started by {current_user.username}: {rec_id}")
        return jsonify({'status': 'ok', 'recording_id': rec_id})
    return jsonify({'error': 'Failed to start recording'}), 500


@recordings_bp.route('/api/recording/stop', methods=['POST'])
@login_required
@require_admin
def api_recording_stop():
    """Stop session recording (admin only)."""
    from app.services.audit.session_recorder import SessionRecorder
    
    config = load_config()
    rec_config = config.get('recording', {})
    recordings_dir = rec_config.get('recordings_dir', '/var/lib/kvm/recordings')
    max_recordings = rec_config.get('max_recordings', 50)
    session_recorder = SessionRecorder(recordings_dir=recordings_dir, max_recordings=max_recordings)
    
    rec_id = session_recorder.stop_recording()
    if rec_id:
        logger.info(f"Recording stopped by {current_user.username}: {rec_id}")
        return jsonify({'status': 'ok', 'recording_id': rec_id})
    return jsonify({'error': 'No active recording'}), 400


@recordings_bp.route('/api/recording/status', methods=['GET'])
@login_required
def api_recording_status():
    """Get current recording status."""
    from app.services.audit.session_recorder import SessionRecorder
    
    config = load_config()
    rec_config = config.get('recording', {})
    recordings_dir = rec_config.get('recordings_dir', '/var/lib/kvm/recordings')
    max_recordings = rec_config.get('max_recordings', 50)
    session_recorder = SessionRecorder(recordings_dir=recordings_dir, max_recordings=max_recordings)
    
    return jsonify({
        'recording': session_recorder.is_recording,
        'recording_id': session_recorder._current_id,
    })


@recordings_bp.route('/api/recordings', methods=['GET'])
@login_required
def api_recordings_list():
    """List all recordings."""
    from app.services.audit.session_recorder import SessionRecorder
    
    config = load_config()
    rec_config = config.get('recording', {})
    recordings_dir = rec_config.get('recordings_dir', '/var/lib/kvm/recordings')
    max_recordings = rec_config.get('max_recordings', 50)
    session_recorder = SessionRecorder(recordings_dir=recordings_dir, max_recordings=max_recordings)
    
    return jsonify({'recordings': session_recorder.list_recordings()})


@recordings_bp.route('/api/recordings/<recording_id>', methods=['GET', 'DELETE'])
@login_required
def api_recording_detail(recording_id):
    """Get or delete a recording."""
    from app.services.audit.session_recorder import SessionRecorder
    
    config = load_config()
    rec_config = config.get('recording', {})
    recordings_dir = rec_config.get('recordings_dir', '/var/lib/kvm/recordings')
    max_recordings = rec_config.get('max_recordings', 50)
    session_recorder = SessionRecorder(recordings_dir=recordings_dir, max_recordings=max_recordings)

    if request.method == 'DELETE':
        if session_recorder.delete_recording(recording_id):
            return jsonify({'status': 'ok'})
        return jsonify({'error': 'Not found'}), 404

    meta = session_recorder.get_recording(recording_id)
    if not meta:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(meta)


@recordings_bp.route('/api/recordings/<recording_id>/frame/<int:frame_num>')
@login_required
def api_recording_frame(recording_id, frame_num):
    """Get a specific frame from a recording as JPEG."""
    from app.services.audit.session_recorder import SessionRecorder
    
    config = load_config()
    rec_config = config.get('recording', {})
    recordings_dir = rec_config.get('recordings_dir', '/var/lib/kvm/recordings')
    max_recordings = rec_config.get('max_recordings', 50)
    session_recorder = SessionRecorder(recordings_dir=recordings_dir, max_recordings=max_recordings)
    
    data = session_recorder.get_frame(recording_id, frame_num)
    if data is None:
        return jsonify({'error': 'Frame not found'}), 404
    from flask import Response
    return Response(data, mimetype='image/jpeg')


@recordings_bp.route('/api/recordings/<recording_id>/events')
@login_required
def api_recording_events(recording_id):
    """Get input events for a recording."""
    from app.services.audit.session_recorder import SessionRecorder
    
    config = load_config()
    rec_config = config.get('recording', {})
    recordings_dir = rec_config.get('recordings_dir', '/var/lib/kvm/recordings')
    max_recordings = rec_config.get('max_recordings', 50)
    session_recorder = SessionRecorder(recordings_dir=recordings_dir, max_recordings=max_recordings)
    
    events = session_recorder.get_events(recording_id)
    return jsonify({'events': events})


@recordings_bp.route('/api/screenshots', methods=['GET'])
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


@recordings_bp.route('/api/screenshots', methods=['POST'])
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


@recordings_bp.route('/api/screenshots/<filename>', methods=['GET'])
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


@recordings_bp.route('/api/screenshots/<filename>', methods=['DELETE'])
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
