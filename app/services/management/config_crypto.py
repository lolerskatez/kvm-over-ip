"""Transparent encrypted config file storage using Fernet symmetric encryption."""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from cryptography.fernet import Fernet, InvalidToken
    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False
    logger.warning("cryptography not installed; config encryption disabled. Run: pip install cryptography")


class ConfigCrypto:
    """
    Provides transparent encryption/decryption for JSON config files.

    When a key file exists, JSON files are stored as .enc (Fernet-encrypted).
    Falls back gracefully to plaintext if cryptography is unavailable.

    Key is auto-generated on first use and stored at key_path (0o600).
    Migration: calling migrate_plaintext() encrypts an existing .json in-place.
    """

    def __init__(self, key_path='./config.key'):
        self.key_path = Path(key_path)
        self._fernet = None
        if _CRYPTO_AVAILABLE:
            self._load_or_generate_key()

    @property
    def enabled(self):
        return _CRYPTO_AVAILABLE and self._fernet is not None

    def _load_or_generate_key(self):
        try:
            if self.key_path.exists():
                key = self.key_path.read_bytes().strip()
            else:
                key = Fernet.generate_key()
                self.key_path.parent.mkdir(parents=True, exist_ok=True)
                self.key_path.write_bytes(key)
                self.key_path.chmod(0o600)
                logger.info(f"Config encryption key generated: {self.key_path}")
            self._fernet = Fernet(key)
        except Exception as e:
            logger.error(f"Failed to load/generate encryption key: {e}")
            self._fernet = None

    # ── Encrypt / Decrypt ─────────────────────────────────────────────────────

    def encrypt_json(self, data: dict) -> bytes:
        """Encrypt a dict to Fernet-encrypted bytes."""
        if not self.enabled:
            raise RuntimeError("Encryption not available")
        raw = json.dumps(data, indent=2).encode('utf-8')
        return self._fernet.encrypt(raw)

    def decrypt_json(self, data: bytes) -> dict:
        """Decrypt Fernet-encrypted bytes to a dict."""
        if not self.enabled:
            raise RuntimeError("Encryption not available")
        raw = self._fernet.decrypt(data)
        return json.loads(raw.decode('utf-8'))

    # ── File-level helpers ─────────────────────────────────────────────────────

    def save(self, path: Path, data: dict) -> bool:
        """
        Save data to disk.
        - If encryption is enabled: writes to <path>.enc, removes <path> plaintext.
        - Otherwise: writes plaintext JSON to <path>.
        """
        path = Path(path)
        try:
            if self.enabled:
                enc_path = path.with_suffix('.enc')
                enc_data = self.encrypt_json(data)
                enc_path.parent.mkdir(parents=True, exist_ok=True)
                enc_path.write_bytes(enc_data)
                enc_path.chmod(0o600)
                # Remove plaintext version once encrypted copy is safely written
                if path.exists():
                    path.unlink()
                return True
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data, indent=2))
                path.chmod(0o640)
                return True
        except Exception as e:
            logger.error(f"ConfigCrypto.save failed for {path}: {e}")
            return False

    def load(self, path: Path):
        """
        Load data from disk.
        - If encryption enabled and <path>.enc exists: decrypt and return dict.
        - Otherwise: read plaintext <path>.
        Returns dict or None on error/missing.
        """
        path = Path(path)
        enc_path = path.with_suffix('.enc')

        if self.enabled and enc_path.exists():
            try:
                return self.decrypt_json(enc_path.read_bytes())
            except InvalidToken:
                logger.error(f"Decryption failed for {enc_path} — wrong key or corrupted file")
                return None
            except Exception as e:
                logger.error(f"ConfigCrypto.load failed for {enc_path}: {e}")
                return None

        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception as e:
                logger.error(f"ConfigCrypto.load plaintext failed for {path}: {e}")
                return None

        return None

    def migrate_plaintext(self, path: Path) -> bool:
        """
        Encrypt an existing plaintext JSON file.
        Returns True if migration occurred, False if nothing to migrate.
        """
        path = Path(path)
        if not self.enabled or not path.exists():
            return False
        enc_path = path.with_suffix('.enc')
        if enc_path.exists():
            # Already migrated
            return False
        try:
            data = json.loads(path.read_text())
            return self.save(path, data)
        except Exception as e:
            logger.error(f"Migration failed for {path}: {e}")
            return False
