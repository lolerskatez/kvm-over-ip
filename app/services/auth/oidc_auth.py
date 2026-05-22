import logging

logger = logging.getLogger(__name__)


class OIDCAuth:
    """
    OIDC / OAuth2 Authorization Code authentication backend.

    Uses the authlib library to implement standard OIDC Authorization Code
    flow with auto-discovery from /.well-known/openid-configuration.

    Config keys (stored in config.json under 'oidc'):
        enabled             Enable OIDC authentication
        issuer_url          Base URL of the IdP (e.g. https://sso.example.com/application/o/kvm/)
        client_id           OAuth2 client ID
        client_secret       OAuth2 client secret
        scope               Space-separated scopes (default: "openid email profile")
        username_claim      Claim to use as the local username (default: "preferred_username")
        admin_claim         Claim name that contains group membership (e.g. "groups")
        admin_claim_value   Claim value that grants admin (e.g. "kvm-admins")
        allowed_groups      List of claim values permitted to log in at all.
                            If empty, only admin_claim_value users may log in via OIDC.
    """

    def __init__(self, config=None):
        self._config = config or {}
        self._available = False
        try:
            from authlib.integrations.requests_client import OAuth2Session  # noqa: F401
            self._available = True
        except ImportError:
            logger.info(
                "authlib not installed — OIDC auth disabled. "
                "Install with: pip install authlib requests"
            )

    @property
    def is_available(self):
        return self._available

    @property
    def is_enabled(self):
        return (
            bool(self._config.get('enabled'))
            and bool(self._config.get('issuer_url'))
            and bool(self._config.get('client_id'))
        )

    def update_config(self, config):
        self._config = config or {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _discover(self):
        """Fetch and return the OIDC discovery document."""
        import requests
        issuer = self._config['issuer_url'].rstrip('/')
        # Support both issuer roots and issuer paths (Authentik, Keycloak, etc.)
        url = f"{issuer}/.well-known/openid-configuration"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_authorization_url(self, redirect_uri, state):
        """
        Build and return the IdP authorization URL.

        The caller should redirect the browser to this URL.
        """
        from authlib.integrations.requests_client import OAuth2Session

        discovery = self._discover()
        scope = self._config.get('scope', 'openid email profile')

        client = OAuth2Session(
            client_id=self._config['client_id'],
            redirect_uri=redirect_uri,
            scope=scope,
        )
        url, _ = client.create_authorization_url(
            discovery['authorization_endpoint'],
            state=state,
        )
        return url

    def exchange_code(self, redirect_uri, code):
        """
        Exchange an authorization code for tokens and return userinfo claims.

        Returns a dict of claims from the userinfo endpoint.
        Raises on any error (network, HTTP, token validation).
        """
        from authlib.integrations.requests_client import OAuth2Session

        discovery = self._discover()

        client = OAuth2Session(
            client_id=self._config['client_id'],
            client_secret=self._config.get('client_secret', ''),
            redirect_uri=redirect_uri,
        )
        client.fetch_token(
            discovery['token_endpoint'],
            code=code,
            grant_type='authorization_code',
        )
        userinfo_resp = client.get(discovery['userinfo_endpoint'])
        userinfo_resp.raise_for_status()
        return userinfo_resp.json()

    def get_username(self, userinfo):
        """Extract a local username string from userinfo claims."""
        claim = self._config.get('username_claim', 'preferred_username')
        username = (
            userinfo.get(claim)
            or userinfo.get('preferred_username')
            or userinfo.get('email')
            or userinfo.get('sub', '')
        )
        return str(username).strip()

    def is_admin(self, userinfo):
        """Return True if userinfo contains the configured admin group claim."""
        claim = self._config.get('admin_claim', '')
        value = self._config.get('admin_claim_value', '')
        if not claim or not value:
            return False
        claim_val = userinfo.get(claim)
        if isinstance(claim_val, list):
            return value in claim_val
        return str(claim_val) == value

    def is_allowed(self, userinfo):
        """
        Return True if the user is permitted to log in via OIDC.

        Admins (matched by admin_claim_value) are always allowed.
        Non-admins are allowed only if their group claim contains at least one
        value from the allowed_groups list.  If allowed_groups is empty, only
        admins may log in via OIDC.
        """
        if self.is_admin(userinfo):
            return True
        allowed = self._config.get('allowed_groups', [])
        if not allowed:
            return False
        claim = self._config.get('admin_claim', '')
        if not claim:
            return False
        claim_val = userinfo.get(claim)
        if isinstance(claim_val, list):
            return any(g in claim_val for g in allowed)
        return str(claim_val) in allowed

    def test_discovery(self):
        """
        Verify the issuer discovery URL is reachable and returns a valid document.

        Returns {'ok': True, ...} on success or {'ok': False, 'error': str} on failure.
        """
        try:
            doc = self._discover()
            return {
                'ok': True,
                'issuer': doc.get('issuer', ''),
                'authorization_endpoint': doc.get('authorization_endpoint', ''),
                'token_endpoint': doc.get('token_endpoint', ''),
            }
        except Exception as e:
            return {'ok': False, 'error': str(e)}
