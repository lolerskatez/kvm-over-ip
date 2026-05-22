"""Dashboard routes blueprint."""

import logging
from flask import Blueprint, render_template, redirect, url_for
from flask_login import login_required, current_user

logger = logging.getLogger(__name__)

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/')
def index():
    """Redirect to login or dashboard."""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.dashboard_page'))
    return redirect(url_for('auth.login'))


@dashboard_bp.route('/dashboard')
@login_required
def dashboard_page():
    """System dashboard page - ensure password change is completed."""
    # Security check: ensure user has completed password change if required
    from app.utils.config import load_users
    
    username = current_user.username
    users = load_users()
    user_data = users.get(username, {})
    
    if user_data.get('requires_password_change', False):
        logger.warning(f"User {username} attempted to access dashboard without changing password")
        return redirect(url_for('auth.change_password'))
    
    return render_template('dashboard.html')
