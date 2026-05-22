import json
import time
from pathlib import Path
from datetime import datetime, timedelta
import threading

class SessionManager:
    """
    Manages user sessions with single-session enforcement.
    Only one user can be logged in at a time.
    Tracks login attempts and alerts.
    """
    
    def __init__(self, sessions_path='/etc/kvm/sessions.json'):
        """
        Initialize session manager.
        
        Args:
            sessions_path: Path to store session data
        """
        self.sessions_path = Path(sessions_path)
        self.sessions = {}
        self.login_attempts = {}
        self.alerts = {}
        self.lock = threading.Lock()
        self._load_sessions()
    
    def _load_sessions(self):
        """Load sessions from file."""
        try:
            if self.sessions_path.exists():
                with open(self.sessions_path, 'r') as f:
                    data = json.load(f)
                    self.sessions = data.get('sessions', {})
                    self.login_attempts = data.get('login_attempts', {})
        except Exception as e:
            print(f"Error loading sessions: {e}")
    
    def _save_sessions(self):
        """Save sessions to file."""
        try:
            self.sessions_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                'sessions': self.sessions,
                'login_attempts': self.login_attempts,
                'timestamp': datetime.utcnow().isoformat()
            }
            with open(self.sessions_path, 'w') as f:
                json.dump(data, f, indent=2)
            self.sessions_path.chmod(0o600)
            return True
        except Exception as e:
            print(f"Error saving sessions: {e}")
            return False
    
    def get_active_session(self):
        """Get currently active session."""
        with self.lock:
            for username, session in self.sessions.items():
                if session.get('active'):
                    if self._is_session_valid(session):
                        return username
                    else:
                        self._invalidate_session(username)
        
        return None
    
    def _is_session_valid(self, session):
        """Check if session is still valid."""
        if not session.get('active'):
            return False
        
        expiry = session.get('expiry')
        if expiry:
            if datetime.fromisoformat(expiry) < datetime.utcnow():
                return False
        
        return True
    
    def _invalidate_session(self, username):
        """Invalidate a session."""
        if username in self.sessions:
            self.sessions[username]['active'] = False
            self.sessions[username]['ended_at'] = datetime.utcnow().isoformat()
    
    def _get_active_session_unlocked(self):
        """Get currently active session (caller must hold self.lock)."""
        for username, session in self.sessions.items():
            if session.get('active'):
                if self._is_session_valid(session):
                    return username
                else:
                    self._invalidate_session(username)
        return None

    def create_session(self, username, session_id, ip_address='', user_agent=''):
        """
        Create a new session for user.
        
        Args:
            username: Username
            session_id: Flask session ID
            ip_address: Client IP address
            user_agent: Client user agent
            
        Returns:
            True if successful, False if another user is logged in
        """
        with self.lock:
            active_user = self._get_active_session_unlocked()
            
            if active_user and active_user != username:
                return False
            
            self.sessions[username] = {
                'active': True,
                'session_id': session_id,
                'ip_address': ip_address,
                'user_agent': user_agent,
                'login_time': datetime.utcnow().isoformat(),
                'expiry': (datetime.utcnow() + timedelta(hours=8)).isoformat(),
                'last_activity': datetime.utcnow().isoformat()
            }
            
            self._save_sessions()
            return True
    
    def end_session(self, username):
        """End a user's session."""
        with self.lock:
            self._invalidate_session(username)
            self._save_sessions()
    
    def update_activity(self, username):
        """Update last activity timestamp."""
        with self.lock:
            if username in self.sessions:
                self.sessions[username]['last_activity'] = datetime.utcnow().isoformat()
                self._save_sessions()
    
    def record_login_attempt(self, username, success, ip_address=''):
        """
        Record a login attempt.
        
        Args:
            username: Username
            success: Whether login was successful
            ip_address: Client IP address
        """
        with self.lock:
            if username not in self.login_attempts:
                self.login_attempts[username] = []
            
            attempt = {
                'timestamp': datetime.utcnow().isoformat(),
                'success': success,
                'ip_address': ip_address
            }
            
            self.login_attempts[username].append(attempt)
            
            # Keep only last 100 attempts per user
            if len(self.login_attempts[username]) > 100:
                self.login_attempts[username] = self.login_attempts[username][-100:]
            
            self._save_sessions()
    
    def get_login_attempts(self, username, hours=24):
        """Get recent login attempts for user."""
        with self.lock:
            if username not in self.login_attempts:
                return []
            
            cutoff = datetime.utcnow() - timedelta(hours=hours)
            attempts = []
            
            for attempt in self.login_attempts[username]:
                attempt_time = datetime.fromisoformat(attempt['timestamp'])
                if attempt_time > cutoff:
                    attempts.append(attempt)
            
            return attempts
    
    def is_brute_force_attempt(self, username, max_attempts=5, window_minutes=15):
        """
        Check if user is experiencing brute force attack.
        
        Args:
            username: Username
            max_attempts: Max failed attempts allowed
            window_minutes: Time window in minutes
            
        Returns:
            True if brute force detected
        """
        with self.lock:
            if username not in self.login_attempts:
                return False
            
            cutoff = datetime.utcnow() - timedelta(minutes=window_minutes)
            failed_attempts = 0
            
            for attempt in self.login_attempts[username]:
                attempt_time = datetime.fromisoformat(attempt['timestamp'])
                if attempt_time > cutoff and not attempt['success']:
                    failed_attempts += 1
            
            return failed_attempts >= max_attempts
    
    def add_alert(self, alert_type, username, message, target_user=None):
        """
        Add an alert for a user.
        
        Args:
            alert_type: Type of alert (login_conflict, login_attempt, etc.)
            username: User to alert
            message: Alert message
            target_user: User who triggered the alert (optional)
        """
        with self.lock:
            if username not in self.alerts:
                self.alerts[username] = []
            
            alert = {
                'type': alert_type,
                'message': message,
                'target_user': target_user,
                'timestamp': datetime.utcnow().isoformat(),
                'read': False
            }
            
            self.alerts[username].append(alert)
            
            # Keep only last 50 alerts per user
            if len(self.alerts[username]) > 50:
                self.alerts[username] = self.alerts[username][-50:]
    
    def get_unread_alerts(self, username):
        """Get unread alerts for user."""
        with self.lock:
            if username not in self.alerts:
                return []
            
            return [a for a in self.alerts[username] if not a['read']]
    
    def get_all_alerts(self, username, limit=20):
        """Get all alerts for user."""
        with self.lock:
            if username not in self.alerts:
                return []
            
            return self.alerts[username][-limit:]
    
    def mark_alerts_read(self, username):
        """Mark all alerts as read for user."""
        with self.lock:
            if username in self.alerts:
                for alert in self.alerts[username]:
                    alert['read'] = True
    
    def is_idle_timeout(self, username, idle_timeout_seconds=900):
        """
        Check if user session has exceeded the idle timeout.
        
        Args:
            username: Username to check
            idle_timeout_seconds: Idle timeout in seconds (default 900 = 15 min)
            
        Returns:
            True if idle timeout exceeded
        """
        with self.lock:
            if username not in self.sessions:
                return False
            sess = self.sessions[username]
            if not sess.get('active'):
                return False
            last = sess.get('last_activity')
            if not last:
                return False
            elapsed = (datetime.utcnow() - datetime.fromisoformat(last)).total_seconds()
            return elapsed > idle_timeout_seconds

    def get_session_info(self, username):
        """Get session information for user."""
        with self.lock:
            if username not in self.sessions:
                return None
            
            session = self.sessions[username]
            if not self._is_session_valid(session):
                return None
            
            return {
                'username': username,
                'login_time': session.get('login_time'),
                'ip_address': session.get('ip_address'),
                'last_activity': session.get('last_activity'),
                'expiry': session.get('expiry')
            }
    
    def cleanup_expired_sessions(self):
        """Remove expired sessions."""
        with self.lock:
            expired = []
            for username, session in self.sessions.items():
                if not self._is_session_valid(session):
                    expired.append(username)
            
            for username in expired:
                del self.sessions[username]
            
            if expired:
                self._save_sessions()
            
            return len(expired)
