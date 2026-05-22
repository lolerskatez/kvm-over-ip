import os
import subprocess
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


class CertManager:
    """
    Manages SSL/TLS certificates for the KVM web interface.

    Supports:
        - Generating self-signed certificates
        - Uploading custom certificate + key pairs
        - Inspecting current certificate details
        - Let's Encrypt integration via acme.sh or certbot
    """

    def __init__(self, cert_dir='.'):
        """
        Initialize certificate manager.

        Args:
            cert_dir: Directory containing cert.pem and key.pem.
        """
        self.cert_dir = Path(cert_dir)
        self.cert_path = self.cert_dir / 'cert.pem'
        self.key_path = self.cert_dir / 'key.pem'

    def has_certificate(self):
        """Check if a certificate and key are present."""
        return self.cert_path.exists() and self.key_path.exists()

    def get_cert_info(self):
        """
        Get details about the current certificate.

        Returns:
            Dict with subject, issuer, expiry, etc. or None.
        """
        if not self.has_certificate():
            return None

        try:
            result = subprocess.run(
                ['openssl', 'x509', '-in', str(self.cert_path),
                 '-noout', '-subject', '-issuer', '-dates', '-serial',
                 '-fingerprint', '-ext', 'subjectAltName'],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return {'error': result.stderr.strip()}

            info = {'raw': result.stdout.strip()}
            for line in result.stdout.strip().splitlines():
                line = line.strip()
                if line.startswith('subject='):
                    info['subject'] = line[len('subject='):].strip()
                elif line.startswith('issuer='):
                    info['issuer'] = line[len('issuer='):].strip()
                elif line.startswith('notBefore='):
                    info['not_before'] = line[len('notBefore='):].strip()
                elif line.startswith('notAfter='):
                    info['not_after'] = line[len('notAfter='):].strip()
                elif line.startswith('serial='):
                    info['serial'] = line[len('serial='):].strip()
                elif 'Fingerprint=' in line:
                    info['fingerprint'] = line.strip()

            # Check if self-signed
            info['self_signed'] = info.get('subject', '') == info.get('issuer', '')
            return info

        except FileNotFoundError:
            return {'error': 'openssl not found. Install with: apk add openssl'}
        except Exception as e:
            return {'error': str(e)}

    def generate_self_signed(self, common_name='kvm-over-ip', days=365,
                             san_names=None):
        """
        Generate a self-signed certificate.

        Args:
            common_name: Certificate CN (hostname).
            days: Validity period in days.
            san_names: Optional list of Subject Alternative Names.

        Returns:
            True on success, False on error.
        """
        try:
            san_ext = ''
            if san_names:
                entries = []
                for name in san_names:
                    if name.replace('.', '').isdigit() or ':' in name:
                        entries.append(f'IP:{name}')
                    else:
                        entries.append(f'DNS:{name}')
                san_ext = ','.join(entries)
            else:
                san_ext = f'DNS:{common_name},DNS:localhost,IP:127.0.0.1'

            cmd = [
                'openssl', 'req', '-x509', '-newkey', 'rsa:2048',
                '-keyout', str(self.key_path),
                '-out', str(self.cert_path),
                '-days', str(days),
                '-nodes',
                '-subj', f'/CN={common_name}',
                '-addext', f'subjectAltName={san_ext}',
            ]

            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
            )

            if result.returncode != 0:
                logger.error(f"openssl error: {result.stderr}")
                return False

            # Restrict permissions
            self.key_path.chmod(0o600)
            self.cert_path.chmod(0o644)

            logger.info(f"Self-signed certificate generated: CN={common_name}, {days} days")
            return True

        except FileNotFoundError:
            logger.error("openssl not found. Install with: apk add openssl")
            return False
        except Exception as e:
            logger.error(f"Certificate generation failed: {e}")
            return False

    def upload_certificate(self, cert_data, key_data):
        """
        Save uploaded certificate and key data.

        Args:
            cert_data: PEM-encoded certificate bytes/string.
            key_data: PEM-encoded private key bytes/string.

        Returns:
            True on success, error string on failure.
        """
        try:
            # Validate cert
            if isinstance(cert_data, bytes):
                cert_data = cert_data.decode('utf-8')
            if isinstance(key_data, bytes):
                key_data = key_data.decode('utf-8')

            if '-----BEGIN CERTIFICATE-----' not in cert_data:
                return 'Invalid certificate: missing PEM header'
            if '-----BEGIN' not in key_data or 'PRIVATE KEY' not in key_data:
                return 'Invalid key: missing PEM private key header'

            # Write to temp files first, then validate
            tmp_cert = self.cert_dir / 'cert.pem.tmp'
            tmp_key = self.cert_dir / 'key.pem.tmp'

            tmp_cert.write_text(cert_data)
            tmp_key.write_text(key_data)

            # Validate the pair matches
            cert_mod = subprocess.run(
                ['openssl', 'x509', '-noout', '-modulus', '-in', str(tmp_cert)],
                capture_output=True, text=True, timeout=10,
            )
            key_mod = subprocess.run(
                ['openssl', 'rsa', '-noout', '-modulus', '-in', str(tmp_key)],
                capture_output=True, text=True, timeout=10,
            )

            if cert_mod.returncode != 0 or key_mod.returncode != 0:
                tmp_cert.unlink(missing_ok=True)
                tmp_key.unlink(missing_ok=True)
                return 'Invalid certificate or key format'

            if cert_mod.stdout.strip() != key_mod.stdout.strip():
                tmp_cert.unlink(missing_ok=True)
                tmp_key.unlink(missing_ok=True)
                return 'Certificate and key do not match'

            # Move into place
            tmp_cert.rename(self.cert_path)
            tmp_key.rename(self.key_path)
            self.key_path.chmod(0o600)
            self.cert_path.chmod(0o644)

            logger.info("Custom certificate uploaded successfully")
            return True

        except FileNotFoundError:
            return 'openssl not found. Install with: apk add openssl'
        except Exception as e:
            logger.error(f"Certificate upload failed: {e}")
            return str(e)

    def delete_certificate(self):
        """Remove the current certificate and key (revert to HTTP)."""
        try:
            if self.cert_path.exists():
                self.cert_path.unlink()
            if self.key_path.exists():
                self.key_path.unlink()
            logger.info("Certificate and key removed")
            return True
        except Exception as e:
            logger.error(f"Failed to delete certificate: {e}")
            return False

    def request_letsencrypt(self, domain, email=None):
        """
        Request a Let's Encrypt certificate using certbot or acme.sh.

        Args:
            domain: Domain name for the certificate.
            email: Contact email for Let's Encrypt.

        Returns:
            True on success, error string on failure.
        """
        # Try certbot first, then acme.sh
        certbot = self._which('certbot')
        acme_sh = self._which('acme.sh') or Path(os.path.expanduser('~/.acme.sh/acme.sh'))

        if certbot:
            return self._letsencrypt_certbot(domain, email, certbot)
        elif acme_sh and Path(acme_sh).exists():
            return self._letsencrypt_acme(domain, email, str(acme_sh))
        else:
            return 'Neither certbot nor acme.sh found. Install with: apk add certbot'

    def _letsencrypt_certbot(self, domain, email, certbot_path):
        """Use certbot standalone mode."""
        cmd = [
            str(certbot_path), 'certonly', '--standalone',
            '-d', domain,
            '--cert-path', str(self.cert_path),
            '--key-path', str(self.key_path),
            '--non-interactive', '--agree-tos',
        ]
        if email:
            cmd += ['--email', email]
        else:
            cmd += ['--register-unsafely-without-email']

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                return f'certbot error: {result.stderr.strip()}'
            logger.info(f"Let's Encrypt certificate obtained for {domain}")
            return True
        except Exception as e:
            return str(e)

    def _letsencrypt_acme(self, domain, email, acme_path):
        """Use acme.sh standalone mode."""
        cmd = [
            acme_path, '--issue', '-d', domain, '--standalone',
            '--cert-file', str(self.cert_path),
            '--key-file', str(self.key_path),
        ]
        if email:
            cmd += ['--accountemail', email]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                return f'acme.sh error: {result.stderr.strip()}'
            logger.info(f"Let's Encrypt certificate obtained for {domain} via acme.sh")
            return True
        except Exception as e:
            return str(e)

    @staticmethod
    def _which(name):
        """Find executable in PATH."""
        import shutil
        return shutil.which(name)

    def check_expiry_days(self):
        """
        Return the number of days until the current certificate expires.
        Returns None if no cert or could not parse. Returns 0 if already expired.
        """
        if not self.has_certificate():
            return None
        try:
            result = subprocess.run(
                ['openssl', 'x509', '-in', str(self.cert_path), '-noout', '-enddate'],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return None
            # enddate=notAfter=May  1 00:00:00 2026 GMT
            line = result.stdout.strip()
            date_str = line.split('=', 1)[1].strip() if '=' in line else line
            expiry = datetime.strptime(date_str, '%b %d %H:%M:%S %Y %Z')
            delta = expiry - datetime.utcnow()
            return max(0, delta.days)
        except Exception as e:
            logger.error(f"check_expiry_days error: {e}")
            return None

    def is_letsencrypt_cert(self):
        """Return True if the current cert was issued by Let's Encrypt."""
        info = self.get_cert_info()
        if not info:
            return False
        issuer = info.get('issuer', '').lower()
        return "let's encrypt" in issuer or 'letsencrypt' in issuer or "r3" in issuer or "r10" in issuer or "r11" in issuer

    def auto_renew(self):
        """
        Attempt to renew the certificate using the same tool that issued it.
        Returns True on success, error string on failure.
        """
        certbot = self._which('certbot')
        acme_sh = self._which('acme.sh') or Path(os.path.expanduser('~/.acme.sh/acme.sh'))

        if certbot:
            try:
                result = subprocess.run(
                    [str(certbot), 'renew', '--non-interactive', '--quiet'],
                    capture_output=True, text=True, timeout=120,
                )
                if result.returncode != 0:
                    return f'certbot renew error: {result.stderr.strip()}'
                logger.info("certbot renew completed")
                return True
            except Exception as e:
                return str(e)

        if acme_sh and Path(str(acme_sh)).exists():
            try:
                result = subprocess.run(
                    [str(acme_sh), '--renew-all', '--force'],
                    capture_output=True, text=True, timeout=120,
                )
                if result.returncode != 0:
                    return f'acme.sh renew error: {result.stderr.strip()}'
                logger.info("acme.sh renew completed")
                return True
            except Exception as e:
                return str(e)

        return 'Neither certbot nor acme.sh found for renewal'
