import json
import logging
from datetime import datetime
from pathlib import Path
import threading

logger = logging.getLogger(__name__)


class AuditLog:
    """
    Centralized audit logging for user actions.
    Logs all user interactions with timestamps and user attribution.
    """
    
    def __init__(self, log_path=None):
        """
        Initialize audit logger.
        
        Args:
            log_path: Path to audit log file. Defaults to /etc/kvm/audit.jsonl or ./audit.jsonl
        """
        if log_path is None:
            if Path('/etc/kvm').exists():
                log_path = Path('/etc/kvm/audit.jsonl')
            else:
                log_path = Path('./audit.jsonl')
        
        self.log_path = Path(log_path)
        self.lock = threading.Lock()
        
        # Create parent directory if needed
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
    
    def log(self, event_type, username=None, details=None, ip_address=None):
        """
        Log an event to the audit trail.
        
        Args:
            event_type: Type of event (login, logout, key_press, mouse_click, 
                       config_change, power_action, session_start, session_end, etc.)
            username: Username performing the action
            details: Additional details dictionary
            ip_address: Client IP address
        """
        entry = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'event_type': event_type,
            'username': username,
            'ip_address': ip_address,
            'details': details or {}
        }
        
        with self.lock:
            try:
                with open(self.log_path, 'a') as f:
                    f.write(json.dumps(entry) + '\n')
            except Exception as e:
                logger.error(f"Failed to write audit log: {e}")
    
    def log_login(self, username, ip_address, success=True, failure_reason=None):
        """Log authentication event."""
        details = {'success': success}
        if failure_reason:
            details['failure_reason'] = failure_reason
        self.log('login', username=username, ip_address=ip_address, details=details)
    
    def log_logout(self, username, ip_address, reason=None):
        """Log logout event."""
        details = {}
        if reason:
            details['reason'] = reason
        self.log('logout', username=username, ip_address=ip_address, details=details)
    
    def log_session_start(self, username, ip_address):
        """Log console session start."""
        self.log('session_start', username=username, ip_address=ip_address)
    
    def log_session_end(self, username, ip_address, duration_sec=None):
        """Log console session end."""
        details = {}
        if duration_sec is not None:
            details['duration_seconds'] = duration_sec
        self.log('session_end', username=username, ip_address=ip_address, details=details)
    
    def log_keyboard_input(self, username, ip_address, keycode=None, text=None, 
                          modifiers=None):
        """Log keyboard input event."""
        details = {}
        if keycode is not None:
            details['keycode'] = keycode
        if text:
            details['text'] = text[:50]  # Truncate for privacy
        if modifiers:
            details['modifiers'] = modifiers
        self.log('keyboard_input', username=username, ip_address=ip_address, 
                details=details)
    
    def log_mouse_movement(self, username, ip_address, x=None, y=None, mode=None):
        """Log mouse movement event."""
        details = {}
        # Only log every Nth movement to reduce log volume
        if x is not None:
            details['x'] = x
        if y is not None:
            details['y'] = y
        if mode:
            details['mode'] = mode
        self.log('mouse_movement', username=username, ip_address=ip_address, 
                details=details)
    
    def log_mouse_click(self, username, ip_address, button, x=None, y=None):
        """Log mouse click event."""
        details = {'button': button}
        if x is not None:
            details['x'] = x
        if y is not None:
            details['y'] = y
        self.log('mouse_click', username=username, ip_address=ip_address, 
                details=details)
    
    def log_config_change(self, username, ip_address, setting_name, old_value, new_value):
        """Log configuration change."""
        details = {
            'setting': setting_name,
            'old_value': str(old_value)[:100],
            'new_value': str(new_value)[:100]
        }
        self.log('config_change', username=username, ip_address=ip_address, 
                details=details)
    
    def log_power_action(self, username, ip_address, action):
        """Log power control action."""
        details = {'action': action}  # power_on, power_off, reset, etc.
        self.log('power_action', username=username, ip_address=ip_address, 
                details=details)
    
    def log_2fa_action(self, username, ip_address, action, success=True):
        """Log 2FA-related actions."""
        details = {'action': action, 'success': success}
        self.log('2fa_action', username=username, ip_address=ip_address, 
                details=details)
    
    def log_macro_execution(self, username, ip_address, macro_name):
        """Log macro execution."""
        details = {'macro_name': macro_name}
        self.log('macro_execution', username=username, ip_address=ip_address, 
                details=details)
    
    def get_logs(self, limit=1000, event_type=None, username=None):
        """
        Retrieve audit logs.
        
        Args:
            limit: Maximum number of log entries to return
            event_type: Filter by event type
            username: Filter by username
        
        Returns:
            List of log entries (most recent first)
        """
        logs = []
        try:
            with open(self.log_path, 'r') as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        if event_type and entry.get('event_type') != event_type:
                            continue
                        if username and entry.get('username') != username:
                            continue
                        logs.append(entry)
                    except json.JSONDecodeError:
                        continue
        except FileNotFoundError:
            pass
        
        # Return most recent first
        return list(reversed(logs))[-limit:]
