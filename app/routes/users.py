"""Users management routes blueprint."""

import logging
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash

from app.utils import require_admin, load_users, save_users

logger = logging.getLogger(__name__)

users_bp = Blueprint('users', __name__)


@users_bp.route('/users')
@login_required
def users_page():
    """User management page (admin only)."""
    users = load_users()
    if not users.get(current_user.username, {}).get('is_admin', False):
        return render_template('404.html'), 404
    return render_template('users.html')


@users_bp.route('/api/users', methods=['GET'])
@login_required
@require_admin
def api_get_users():
    """Get user list (admin only)."""
    from app.services.auth.totp_manager import TOTPManager
    from app.utils.config import get_config_paths
    
    paths = get_config_paths()
    totp_manager = TOTPManager(paths['totp'])
    users = load_users()
    
    user_list = []
    for username, user_data in users.items():
        totp_status = totp_manager.get_user_2fa_status(username)
        is_admin = user_data.get('is_admin', False)
        role = user_data.get('role', 'admin' if is_admin else 'operator')
        user_list.append({
            'username': username,
            'is_admin': is_admin,
            'role': role,
            '2fa_enabled': totp_status['enabled']
        })
    
    return jsonify({'users': user_list})


@users_bp.route('/api/users', methods=['POST'])
@login_required
@require_admin
def api_create_user():
    """Create new user (admin only)."""
    users = load_users()
    
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '')
    is_admin = data.get('is_admin', False)
    role = data.get('role', 'admin' if is_admin else 'operator')
    # Normalise role
    if role not in ('admin', 'operator', 'viewer'):
        role = 'operator'
    # Keep is_admin in sync
    if role == 'admin':
        is_admin = True
    
    if not username or not password:
        return jsonify({'error': 'Username and password required'}), 400
    
    if username in users:
        return jsonify({'error': 'User already exists'}), 409
    
    if len(username) < 3 or len(username) > 32:
        return jsonify({'error': 'Username must be 3-32 characters'}), 400
    
    if len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400
    
    users[username] = {
        'password': generate_password_hash(password),
        'is_admin': is_admin,
        'role': role,
    }
    
    if save_users(users):
        logger.info(f"User {username} created by {current_user.username} (role={role})")
        return jsonify({'status': 'ok', 'username': username}), 201
    else:
        return jsonify({'error': 'Failed to create user'}), 500


@users_bp.route('/api/users/<username>', methods=['PUT'])
@login_required
@require_admin
def api_update_user(username):
    """Update user (admin only)."""
    users = load_users()
    
    if username not in users:
        return jsonify({'error': 'User not found'}), 404
    
    data = request.get_json()
    
    if 'role' in data:
        role = data['role']
        if role not in ('admin', 'operator', 'viewer'):
            return jsonify({'error': 'Invalid role. Use admin, operator, or viewer'}), 400
        users[username]['role'] = role
        users[username]['is_admin'] = (role == 'admin')
    elif 'is_admin' in data:
        users[username]['is_admin'] = data['is_admin']
        # Keep role in sync
        if data['is_admin']:
            users[username]['role'] = 'admin'
        elif users[username].get('role') == 'admin':
            users[username]['role'] = 'operator'
    
    if 'password' in data and data['password']:
        if len(data['password']) < 8:
            return jsonify({'error': 'Password must be at least 8 characters'}), 400
        users[username]['password'] = generate_password_hash(data['password'])
    
    if save_users(users):
        logger.info(f"User {username} updated by {current_user.username}")
        return jsonify({'status': 'ok'})
    else:
        return jsonify({'error': 'Failed to update user'}), 500


@users_bp.route('/api/users/<username>', methods=['DELETE'])
@login_required
@require_admin
def api_delete_user(username):
    """Delete user (admin only)."""
    users = load_users()
    
    if username == current_user.username:
        return jsonify({'error': 'Cannot delete your own account'}), 400
    
    if username not in users:
        return jsonify({'error': 'User not found'}), 404
    
    del users[username]
    
    if save_users(users):
        logger.info(f"User {username} deleted by {current_user.username}")
        return jsonify({'status': 'ok'})
    else:
        return jsonify({'error': 'Failed to delete user'}), 500
