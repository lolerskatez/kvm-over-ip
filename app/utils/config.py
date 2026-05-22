"""Centralized configuration management."""

import os
import json
import re
import logging
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def get_config_paths():
    """Get configuration file paths based on environment.
    
    Returns:
        dict: Contains paths for config, users, sessions, totp, and crypto key files.
              Production paths use /etc/kvm/, development uses current directory.
    """
    base_dir = Path('/etc/kvm') if os.path.exists('/etc/kvm') else Path('.')
    
    return {
        'config': base_dir / 'config.json',
        'users': base_dir / 'users.json',
        'sessions': str(base_dir / 'sessions.json'),
        'totp': str(base_dir / 'totp_secrets.json'),
        'crypto_key': base_dir / 'config.key',
    }


def load_config() -> Dict[str, Any]:
    """Load configuration from JSON file.
    
    Returns:
        dict: Configuration dictionary. Returns default config if file not found.
    """
    paths = get_config_paths()
    config_path = paths['config']
    
    try:
        if config_path.exists():
            with open(config_path, 'r') as f:
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


def save_config(config: Dict[str, Any]) -> bool:
    """Save configuration to JSON file.
    
    Args:
        config: Configuration dictionary to save.
        
    Returns:
        bool: True if successful, False otherwise.
    """
    paths = get_config_paths()
    config_path = paths['config']
    
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Failed to save config: {e}")
        return False


def load_users() -> Dict[str, Any]:
    """Load users from JSON file (or encrypted .enc file).
    
    Returns:
        dict: Users dictionary. Empty dict if file not found.
    """
    from app.services.management.config_crypto import ConfigCrypto
    
    paths = get_config_paths()
    users_path = paths['users']
    
    try:
        crypto = ConfigCrypto(key_path=str(paths['crypto_key']))
        data = crypto.load(users_path)
        if data is not None:
            return data
    except Exception as e:
        logger.error(f"Failed to load users: {e}")
    
    return {}


def save_users(users: Dict[str, Any]) -> bool:
    """Save users to disk (encrypted if config.key exists, plaintext otherwise).
    
    Args:
        users: Users dictionary to save.
        
    Returns:
        bool: True if successful, False otherwise.
    """
    from app.services.management.config_crypto import ConfigCrypto
    
    paths = get_config_paths()
    users_path = paths['users']
    
    try:
        users_path.parent.mkdir(parents=True, exist_ok=True)
        crypto = ConfigCrypto(key_path=str(paths['crypto_key']))
        return crypto.save(users_path, users)
    except Exception as e:
        logger.error(f"Failed to save users: {e}")
        return False


def validate_config_value(key: str, value: Any) -> bool:
    """Validate configuration values to prevent injection.
    
    Args:
        key: Configuration key name.
        value: Configuration value to validate.
        
    Returns:
        bool: True if valid, False otherwise.
    """
    if key in ('video_device', 'hid_device'):
        if not isinstance(value, str) or not re.match(r'^/dev/[a-zA-Z0-9_/]+$', value):
            return False
    elif key == 'resolution':
        if not isinstance(value, str) or not re.match(r'^\d{3,4}x\d{3,4}$', value):
            return False
    elif key == 'framerate':
        if not isinstance(value, int) or value < 1 or value > 60:
            return False
    elif key == 'bitrate':
        if not isinstance(value, str) or not re.match(r'^\d+k$', value):
            return False
    elif key == 'idle_timeout':
        if not isinstance(value, int) or value < 0 or value > 86400:
            return False
    return True


def ensure_password_change_flag() -> None:
    """Migrate existing users to require password change on next login if flag not set.
    
    This ensures that users from previous installations are forced to change
    their password to a new one (especially important for default credentials).
    """
    try:
        users = load_users()
        needs_save = False
        for username, user_data in users.items():
            if 'requires_password_change' not in user_data:
                user_data['requires_password_change'] = True
                needs_save = True
                logger.info(f"Marked user {username} for password change migration")
        if needs_save:
            save_users(users)
            logger.info("Password change flag migration completed")
    except Exception as e:
        logger.error(f"Failed to migrate password change flags: {e}")
