"""Console routes blueprint."""

import logging
from flask import Blueprint, render_template
from flask_login import login_required

logger = logging.getLogger(__name__)

console_bp = Blueprint('console', __name__)


@console_bp.route('/console')
@login_required
def console():
    """Console interface for KVM control."""
    return render_template('console.html')
