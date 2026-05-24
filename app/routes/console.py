"""Console routes blueprint."""

import logging
from flask import Blueprint, render_template, redirect, url_for
from flask_login import login_required

from app.utils import load_config

logger = logging.getLogger(__name__)

console_bp = Blueprint('console', __name__)


@console_bp.route('/console')
@login_required
def console():
    """Console interface for KVM control (auto-detects mode from config)."""
    config = load_config()
    streaming_mode = config.get('streaming_mode', 'mjpeg')
    
    # Redirect to appropriate console based on streaming_mode
    if streaming_mode == 'webrtc':
        return redirect(url_for('console.console_webrtc'))
    else:
        return render_template('console.html')


@console_bp.route('/console-mjpeg')
@login_required
def console_mjpeg():
    """Console interface for KVM control (MJPEG mode - legacy)."""
    return render_template('console.html')


@console_bp.route('/console-webrtc')
@login_required
def console_webrtc():
    """Console interface for KVM control (WebRTC mode - low latency)."""
    return render_template('console_webrtc.html')
