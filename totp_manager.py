import pyotp
import qrcode
import io
import base64
import json
from pathlib import Path

class TOTPManager:
    """
    Time-based One-Time Password (TOTP) manager for 2FA.
    Uses RFC 6238 standard with 30-second time steps.
    """
    
    def __init__(self, secrets_path='/etc/kvm/totp_secrets.json'):
        """
        Initialize TOTP manager.
        
        Args:
            secrets_path: Path to store TOTP secrets
        """
        self.secrets_path = Path(secrets_path)
        self.secrets = self._load_secrets()
    
    def _load_secrets(self):
        """Load TOTP secrets from file."""
        try:
            if self.secrets_path.exists():
                with open(self.secrets_path, 'r') as f:
                    return json.load(f)
        except Exception as e:
            print(f"Error loading TOTP secrets: {e}")
        
        return {}
    
    def _save_secrets(self):
        """Save TOTP secrets to file."""
        try:
            self.secrets_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.secrets_path, 'w') as f:
                json.dump(self.secrets, f, indent=2)
            # Restrict permissions
            self.secrets_path.chmod(0o600)
            return True
        except Exception as e:
            print(f"Error saving TOTP secrets: {e}")
            return False
    
    def generate_secret(self, username):
        """
        Generate a new TOTP secret for a user.
        
        Args:
            username: Username to generate secret for
            
        Returns:
            Tuple of (secret, provisioning_uri)
        """
        secret = pyotp.random_base32()
        totp = pyotp.TOTP(secret)
        
        provisioning_uri = totp.provisioning_uri(
            name=username,
            issuer_name='KVM-over-IP'
        )
        
        return secret, provisioning_uri
    
    def get_qr_code(self, username, secret):
        """
        Generate QR code for TOTP secret.
        
        Args:
            username: Username
            secret: TOTP secret
            
        Returns:
            Base64-encoded PNG image
        """
        totp = pyotp.TOTP(secret)
        provisioning_uri = totp.provisioning_uri(
            name=username,
            issuer_name='KVM-over-IP'
        )
        
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(provisioning_uri)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white")
        
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        img_str = base64.b64encode(buffer.getvalue()).decode()
        
        return f"data:image/png;base64,{img_str}"
    
    def enable_2fa(self, username, secret):
        """
        Enable 2FA for a user.
        
        Args:
            username: Username
            secret: TOTP secret
            
        Returns:
            True if successful
        """
        if username not in self.secrets:
            self.secrets[username] = {}
        
        self.secrets[username]['secret'] = secret
        self.secrets[username]['enabled'] = True
        self.secrets[username]['backup_codes'] = self._generate_backup_codes()
        
        return self._save_secrets()
    
    def disable_2fa(self, username):
        """
        Disable 2FA for a user.
        
        Args:
            username: Username
            
        Returns:
            True if successful
        """
        if username in self.secrets:
            self.secrets[username]['enabled'] = False
            self.secrets[username]['secret'] = None
            self.secrets[username]['backup_codes'] = []
            return self._save_secrets()
        
        return False
    
    def is_2fa_enabled(self, username):
        """Check if 2FA is enabled for user."""
        if username not in self.secrets:
            return False
        
        return self.secrets[username].get('enabled', False)
    
    def verify_token(self, username, token):
        """
        Verify a TOTP token.
        
        Args:
            username: Username
            token: 6-digit TOTP token
            
        Returns:
            True if token is valid
        """
        if not self.is_2fa_enabled(username):
            return True
        
        secret = self.secrets[username].get('secret')
        if not secret:
            return False
        
        totp = pyotp.TOTP(secret)
        
        # Allow for time drift (±1 time step)
        return totp.verify(token, valid_window=1)
    
    def verify_backup_code(self, username, code):
        """
        Verify and consume a backup code.
        
        Args:
            username: Username
            code: Backup code
            
        Returns:
            True if code is valid and consumed
        """
        if username not in self.secrets:
            return False
        
        backup_codes = self.secrets[username].get('backup_codes', [])
        
        if code in backup_codes:
            backup_codes.remove(code)
            self.secrets[username]['backup_codes'] = backup_codes
            self._save_secrets()
            return True
        
        return False
    
    def get_backup_codes(self, username):
        """Get remaining backup codes for user."""
        if username not in self.secrets:
            return []
        
        return self.secrets[username].get('backup_codes', [])
    
    def _generate_backup_codes(self, count=10):
        """Generate backup codes."""
        import secrets
        codes = []
        for _ in range(count):
            code = secrets.token_hex(4).upper()
            codes.append(code)
        
        return codes
    
    def get_user_2fa_status(self, username):
        """Get 2FA status for user."""
        if username not in self.secrets:
            return {
                'enabled': False,
                'backup_codes_remaining': 0
            }
        
        user_data = self.secrets[username]
        return {
            'enabled': user_data.get('enabled', False),
            'backup_codes_remaining': len(user_data.get('backup_codes', []))
        }
