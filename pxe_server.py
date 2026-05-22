import os
import json
import shutil
import signal
import subprocess
import threading
import logging
from pathlib import Path

from boot_catalog import BOOT_CATALOG

logger = logging.getLogger(__name__)


class PXEServer:
    """
    PXE Boot Server manager.

    Uses dnsmasq in proxy-DHCP mode + TFTP to serve iPXE bootloaders,
    then iPXE chainloads a dynamic boot menu served over HTTP by the
    Flask application. This avoids interfering with existing DHCP
    infrastructure on the network.

    Typical boot flow:
        1. Target PXE ROM → DHCP discover
        2. dnsmasq proxy-DHCP → offers TFTP bootloader path
        3. Target downloads iPXE (undionly.kpxe / ipxe.efi) via TFTP
        4. iPXE fetches boot menu script from KVM HTTP server
        5. User selects an image → iPXE boots kernel+initrd over HTTP

    Directory layout (under base_dir):
        tftp/                TFTP root served by dnsmasq
          ipxe/              iPXE bootloader binaries
        images/              Uploaded ISOs / kernels / initrds
        dnsmasq.pxe.conf     Generated dnsmasq configuration
    """

    # Well-known paths where Alpine's ipxe package installs binaries
    IPXE_SEARCH_PATHS = [
        '/usr/share/ipxe',
        '/usr/lib/ipxe',
        '/boot/ipxe',
    ]

    IPXE_BIOS_NAMES = ['undionly.kpxe', 'ipxe.kpxe']
    IPXE_EFI_NAMES = ['ipxe-x86_64.efi', 'ipxe.efi', 'snponly.efi']

    def __init__(self, base_dir='/var/lib/kvm/pxe', http_port=5000, http_host='0.0.0.0'):
        """
        Initialize PXE server manager.

        Args:
            base_dir: Root directory for PXE files (TFTP root, images, config).
            http_port: Port the Flask app listens on (for iPXE HTTP URLs).
            http_host: Bind address of the Flask app.
        """
        self.base_dir = Path(base_dir)
        self.tftp_dir = self.base_dir / 'tftp'
        self.ipxe_dir = self.tftp_dir / 'ipxe'
        self.images_dir = self.base_dir / 'images'
        self.config_file = self.base_dir / 'dnsmasq.pxe.conf'
        self.pid_file = self.base_dir / 'dnsmasq.pxe.pid'
        self.log_file = self.base_dir / 'dnsmasq.pxe.log'

        self.http_port = http_port
        self.http_host = http_host

        # Configurable settings (persisted via the main app config.json)
        self.interface = ''        # network interface, e.g. 'eth0' ('' = all)
        self.dhcp_range = ''       # e.g. '192.168.1.0' for proxy mode
        self.server_ip = ''        # IP of this machine on the PXE network

        # netboot.xyz chainload — lets clients boot 60+ OSes without local storage
        self.netbootxyz_enabled = True
        self.netbootxyz_url = 'https://boot.netboot.xyz/ipxe/netboot.xyz.lkrn'
        self.netbootxyz_efi_url = 'https://boot.netboot.xyz/ipxe/netboot.xyz.efi'

        # Catalog entries (IDs from boot_catalog.BOOT_CATALOG) to include in the menu
        self.enabled_catalog_ids = []

        self._process = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Directory & dependency setup
    # ------------------------------------------------------------------

    def setup_directories(self):
        """Create required directory structure."""
        for d in (self.tftp_dir, self.ipxe_dir, self.images_dir):
            d.mkdir(parents=True, exist_ok=True)
        logger.info(f"PXE directories ready under {self.base_dir}")

    def _find_ipxe_binary(self, names):
        """Search system paths for an iPXE binary matching one of *names*."""
        for search_dir in self.IPXE_SEARCH_PATHS:
            for name in names:
                candidate = Path(search_dir) / name
                if candidate.exists():
                    return candidate
        return None

    def setup_ipxe(self):
        """
        Copy iPXE bootloader binaries into the TFTP directory.

        Returns a dict with 'bios' and 'efi' keys indicating which
        bootloaders were found/installed. Missing bootloaders are
        logged as warnings.
        """
        self.setup_directories()
        result = {'bios': False, 'efi': False}

        # BIOS bootloader
        bios_src = self._find_ipxe_binary(self.IPXE_BIOS_NAMES)
        bios_dest = self.ipxe_dir / 'undionly.kpxe'
        if bios_src:
            shutil.copy2(str(bios_src), str(bios_dest))
            result['bios'] = True
            logger.info(f"iPXE BIOS bootloader installed from {bios_src}")
        elif bios_dest.exists():
            result['bios'] = True
        else:
            logger.warning("iPXE BIOS bootloader not found. Install ipxe package: apk add ipxe")

        # UEFI bootloader
        efi_src = self._find_ipxe_binary(self.IPXE_EFI_NAMES)
        efi_dest = self.ipxe_dir / 'ipxe.efi'
        if efi_src:
            shutil.copy2(str(efi_src), str(efi_dest))
            result['efi'] = True
            logger.info(f"iPXE EFI bootloader installed from {efi_src}")
        elif efi_dest.exists():
            result['efi'] = True
        else:
            logger.warning("iPXE EFI bootloader not found. Install ipxe package: apk add ipxe")

        return result

    def check_dependencies(self):
        """
        Check that required system tools are available.

        Returns:
            dict with 'dnsmasq' and 'ipxe' status booleans plus messages.
        """
        status = {
            'dnsmasq': False,
            'ipxe_bios': False,
            'ipxe_efi': False,
            'messages': [],
        }

        # dnsmasq
        if shutil.which('dnsmasq'):
            status['dnsmasq'] = True
        else:
            status['messages'].append('dnsmasq not found. Install with: apk add dnsmasq')

        # iPXE binaries (check TFTP dir first, then system paths)
        if (self.ipxe_dir / 'undionly.kpxe').exists() or self._find_ipxe_binary(self.IPXE_BIOS_NAMES):
            status['ipxe_bios'] = True
        else:
            status['messages'].append('iPXE BIOS bootloader not found. Install with: apk add ipxe')

        if (self.ipxe_dir / 'ipxe.efi').exists() or self._find_ipxe_binary(self.IPXE_EFI_NAMES):
            status['ipxe_efi'] = True
        else:
            status['messages'].append('iPXE EFI bootloader not found. Install with: apk add ipxe')

        return status

    # ------------------------------------------------------------------
    # iPXE boot menu generation
    # ------------------------------------------------------------------

    def generate_boot_menu(self):
        """
        Generate an iPXE boot menu script listing all available images.

        Returns the script as a string. This is served dynamically by
        the Flask app at /pxe/boot.ipxe so it always reflects the
        current image library.
        """
        base_url = self._http_base_url()
        images = self.list_images()

        # Determine the default menu item
        default_item = 'netbootxyz_bios' if (self.netbootxyz_enabled and not images) else 'exit'

        lines = [
            '#!ipxe',
            '',
            f'set menu-timeout 30000',
            f'set menu-default {default_item}',
            '',
            ':start',
            'menu KVM-over-IP PXE Boot Menu',
            '',
        ]

        # netboot.xyz entries at the top — architecture-aware chainload
        if self.netbootxyz_enabled:
            lines += [
                'item --gap -- netboot.xyz (remote 60+ OS installer)',
                'item netbootxyz_bios   netboot.xyz — Legacy BIOS (PCBIOS)',
                'item netbootxyz_efi    netboot.xyz — UEFI (EFI)',
            ]

        # Enabled catalog entries — kernel/initrd fetched directly from upstream
        catalog_entries = []
        if self.enabled_catalog_ids:
            catalog_by_id = {e['id']: e for e in BOOT_CATALOG}
            categories = {}
            for cid in self.enabled_catalog_ids:
                entry = catalog_by_id.get(cid)
                if entry:
                    catalog_entries.append(entry)
                    cat = entry.get('category', 'Network OS')
                    categories.setdefault(cat, []).append(entry)
            for cat_name, cat_entries in categories.items():
                lines.append(f'item --gap -- {cat_name}')
                for entry in cat_entries:
                    lines.append(f'item cat_{entry["id"]}  {entry["name"]}')

        # Local images
        if images:
            lines.append('item --gap -- Local Images')
        for img in images:
            name = img['name']
            label = 'img_' + name.replace('.', '_').replace('-', '_').replace(' ', '_')
            lines.append(f'item {label} {name} ({img["size_human"]})')

        lines += [
            'item --gap --',
            'item exit   Exit and continue local boot',
            '',
            'choose --timeout ${menu-timeout} --default ${menu-default} selected',
            'goto ${selected}',
            '',
        ]

        # netboot.xyz boot targets
        if self.netbootxyz_enabled:
            lines += [
                ':netbootxyz_bios',
                f'chain --autofree {self.netbootxyz_url} || goto start',
                '',
                ':netbootxyz_efi',
                f'chain --autofree {self.netbootxyz_efi_url} || goto start',
                '',
            ]

        # Catalog boot targets — fetch directly from upstream URLs
        for entry in catalog_entries:
            label = 'cat_' + entry['id']
            kargs = entry.get('kernel_args', '').strip()
            kern_line = f'kernel {entry["kernel_url"]}'
            if kargs:
                kern_line += f' {kargs}'
            block = [f':{label}', kern_line]
            if entry.get('initrd_url'):
                block.append(f'initrd {entry["initrd_url"]}')
            block += ['boot || goto start', '']
            lines += block

        for img in images:
            name = img['name']
            label = 'img_' + name.replace('.', '_').replace('-', '_').replace(' ', '_')
            boot_type = img.get('boot_type', 'memdisk')

            if boot_type == 'kernel':
                # Linux kernel+initrd style boot
                lines += [
                    f':{label}',
                    f'kernel {base_url}/pxe/images/{name}/vmlinuz {img.get("kernel_args", "")}',
                    f'initrd {base_url}/pxe/images/{name}/initrd.img',
                    'boot || goto start',
                    '',
                ]
            elif boot_type == 'imgload':
                # UEFI-bootable ISO via iPXE imgload
                lines += [
                    f':{label}',
                    f'imgload {base_url}/pxe/images/{name} || goto start',
                    'imgexec || goto start',
                    '',
                ]
            else:
                # ISO via syslinux memdisk (works for legacy BIOS ISOs)
                lines += [
                    f':{label}',
                    f'kernel {base_url}/pxe/memdisk iso raw',
                    f'initrd {base_url}/pxe/images/{name}',
                    'boot || goto start',
                    '',
                ]

        lines += [
            ':exit',
            'exit',
            '',
        ]

        return '\n'.join(lines)

    # ------------------------------------------------------------------
    # dnsmasq configuration & lifecycle
    # ------------------------------------------------------------------

    def _http_base_url(self):
        """Build the HTTP base URL that iPXE will use to fetch files."""
        ip = self.server_ip or self._detect_server_ip()
        scheme = 'http'
        return f'{scheme}://{ip}:{self.http_port}'

    def _detect_server_ip(self):
        """Best-effort detection of this machine's LAN IP address."""
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.5)
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return '127.0.0.1'

    def _generate_dnsmasq_config(self):
        """Generate dnsmasq configuration for PXE proxy DHCP + TFTP."""
        base_url = self._http_base_url()
        boot_menu_url = f'{base_url}/pxe/boot.ipxe'

        lines = [
            '# Auto-generated by KVM-over-IP PXE server — do not edit manually',
            '',
            '# Do not provide DNS',
            'port=0',
            '',
            '# Logging',
            f'log-facility={self.log_file}',
            'log-dhcp',
            '',
            '# TFTP server',
            'enable-tftp',
            f'tftp-root={self.tftp_dir}',
            '',
            '# PID file',
            f'pid-file={self.pid_file}',
            '',
        ]

        # Network interface
        if self.interface:
            lines.append(f'interface={self.interface}')
            lines.append('')

        # Proxy DHCP mode (does not assign IPs, co-exists with existing DHCP)
        if self.dhcp_range:
            lines.append(f'dhcp-range={self.dhcp_range},proxy')
        else:
            lines.append('# WARNING: no dhcp-range set; dnsmasq will not respond to DHCP')
            lines.append('# Set dhcp-range to your subnet, e.g. 192.168.1.0,proxy')

        lines += [
            '',
            '# PXE boot options — detect BIOS vs UEFI client',
            '# Tag UEFI clients (architecture 7 = x64 UEFI, 9 = EBC, etc.)',
            'dhcp-match=set:efi-x86_64,option:client-arch,7',
            'dhcp-match=set:efi-x86_64,option:client-arch,9',
            'dhcp-match=set:bios,option:client-arch,0',
            '',
            '# Serve iPXE for initial PXE clients, boot menu for iPXE clients',
            '# Detect iPXE via user-class option',
            'dhcp-userclass=set:ipxe,iPXE',
            '',
            '# Non-iPXE BIOS clients → iPXE BIOS bootloader',
            'dhcp-boot=tag:bios,tag:!ipxe,ipxe/undionly.kpxe',
            '',
            '# Non-iPXE UEFI clients → iPXE EFI bootloader',
            'dhcp-boot=tag:efi-x86_64,tag:!ipxe,ipxe/ipxe.efi',
            '',
            f'# iPXE clients → fetch boot menu from HTTP',
            f'dhcp-boot=tag:ipxe,{boot_menu_url}',
            '',
        ]

        return '\n'.join(lines)

    def write_config(self):
        """Write dnsmasq configuration file."""
        self.setup_directories()
        config_text = self._generate_dnsmasq_config()
        self.config_file.write_text(config_text, encoding='utf-8')
        logger.info(f"PXE dnsmasq config written to {self.config_file}")
        return True

    def start(self):
        """Start the dnsmasq PXE server."""
        with self._lock:
            if self.is_running():
                logger.warning("PXE server is already running")
                return True

            # Check for dnsmasq
            if not shutil.which('dnsmasq'):
                logger.error("dnsmasq not found. Install with: apk add dnsmasq")
                return False

            # Ensure iPXE binaries and config are in place
            self.setup_ipxe()
            self.write_config()

            try:
                cmd = [
                    'dnsmasq',
                    f'--conf-file={self.config_file}',
                    '--keep-in-foreground',
                    '--no-daemon',
                ]

                self._process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )

                logger.info(f"PXE server started (pid {self._process.pid})")
                return True

            except Exception as e:
                logger.error(f"Failed to start PXE server: {e}")
                self._process = None
                return False

    def stop(self):
        """Stop the dnsmasq PXE server."""
        with self._lock:
            if self._process:
                try:
                    self._process.terminate()
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                except Exception as e:
                    logger.error(f"Error stopping PXE server: {e}")
                finally:
                    self._process = None
                    logger.info("PXE server stopped")
            elif self.pid_file.exists():
                # Process started outside our control — try PID file
                try:
                    pid = int(self.pid_file.read_text().strip())
                    os.kill(pid, signal.SIGTERM)
                    logger.info(f"Sent SIGTERM to dnsmasq pid {pid}")
                except Exception as e:
                    logger.warning(f"Could not kill dnsmasq via PID file: {e}")
                finally:
                    self.pid_file.unlink(missing_ok=True)

    def restart(self):
        """Restart the PXE server (stop + start)."""
        self.stop()
        return self.start()

    def is_running(self):
        """Check if the dnsmasq PXE server process is running."""
        if self._process:
            return self._process.poll() is None
        return False

    def get_status(self):
        """
        Get comprehensive PXE server status.

        Returns:
            dict with running state, configuration, dependencies, and image count.
        """
        deps = self.check_dependencies()
        images = self.list_images()
        return {
            'running': self.is_running(),
            'interface': self.interface or '(all)',
            'dhcp_range': self.dhcp_range or '(not set)',
            'server_ip': self.server_ip or self._detect_server_ip(),
            'image_count': len(images),
            'dependencies': deps,
            'tftp_dir': str(self.tftp_dir),
            'images_dir': str(self.images_dir),
        }

    # ------------------------------------------------------------------
    # Image management
    # ------------------------------------------------------------------

    def list_images(self):
        """
        List available boot images.

        Returns:
            list of dicts with image metadata.
        """
        images = []
        if not self.images_dir.exists():
            return images

        for entry in sorted(self.images_dir.iterdir()):
            if entry.is_file():
                size = entry.stat().st_size
                images.append({
                    'name': entry.name,
                    'size': size,
                    'size_human': self._human_size(size),
                    'boot_type': self._detect_boot_type(entry),
                })
            elif entry.is_dir():
                # Directory-based image (extracted kernel+initrd)
                meta_file = entry / 'meta.json'
                meta = {}
                if meta_file.exists():
                    try:
                        meta = json.loads(meta_file.read_text())
                    except Exception:
                        pass
                total_size = sum(f.stat().st_size for f in entry.rglob('*') if f.is_file())
                images.append({
                    'name': entry.name,
                    'size': total_size,
                    'size_human': self._human_size(total_size),
                    'boot_type': meta.get('boot_type', 'kernel'),
                    'kernel_args': meta.get('kernel_args', ''),
                })

        return images

    def save_image(self, filename, stream):
        """
        Save an uploaded image file.

        Args:
            filename: Sanitised filename.
            stream: File-like object to read from (e.g. request.stream).

        Returns:
            dict with image info, or None on error.
        """
        self.images_dir.mkdir(parents=True, exist_ok=True)
        dest = self.images_dir / filename

        try:
            with open(dest, 'wb') as f:
                while True:
                    chunk = stream.read(64 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)

            size = dest.stat().st_size
            logger.info(f"Image saved: {filename} ({self._human_size(size)})")
            return {
                'name': filename,
                'size': size,
                'size_human': self._human_size(size),
                'boot_type': self._detect_boot_type(dest),
            }
        except Exception as e:
            logger.error(f"Failed to save image {filename}: {e}")
            if dest.exists():
                dest.unlink()
            return None

    def delete_image(self, name):
        """
        Delete a boot image by name.

        Args:
            name: Image filename or directory name.

        Returns:
            True if deleted, False otherwise.
        """
        target = self.images_dir / name
        if not target.exists():
            return False

        # Prevent path traversal
        try:
            target.resolve().relative_to(self.images_dir.resolve())
        except ValueError:
            logger.warning(f"Path traversal attempt blocked: {name}")
            return False

        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            logger.info(f"Image deleted: {name}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete image {name}: {e}")
            return False

    def get_image_path(self, name):
        """
        Resolve a safe path to an image file.

        Returns:
            Path object if valid and exists, None otherwise.
        """
        target = self.images_dir / name
        if not target.exists():
            return None
        # Prevent path traversal
        try:
            target.resolve().relative_to(self.images_dir.resolve())
        except ValueError:
            return None
        return target

    # ------------------------------------------------------------------
    # Configuration persistence helpers
    # ------------------------------------------------------------------

    def apply_config(self, pxe_config):
        """
        Apply PXE settings from a config dict (loaded from config.json).

        Args:
            pxe_config: dict with keys like 'interface', 'dhcp_range',
                        'server_ip', 'base_dir'.
        """
        if not pxe_config:
            return

        if 'base_dir' in pxe_config:
            bd = Path(pxe_config['base_dir'])
            self.base_dir = bd
            self.tftp_dir = bd / 'tftp'
            self.ipxe_dir = self.tftp_dir / 'ipxe'
            self.images_dir = bd / 'images'
            self.config_file = bd / 'dnsmasq.pxe.conf'
            self.pid_file = bd / 'dnsmasq.pxe.pid'
            self.log_file = bd / 'dnsmasq.pxe.log'

        self.interface = pxe_config.get('interface', self.interface)
        self.dhcp_range = pxe_config.get('dhcp_range', self.dhcp_range)
        self.server_ip = pxe_config.get('server_ip', self.server_ip)

        if 'http_port' in pxe_config:
            self.http_port = int(pxe_config['http_port'])

        if 'netbootxyz_enabled' in pxe_config:
            val = pxe_config['netbootxyz_enabled']
            self.netbootxyz_enabled = bool(val) if isinstance(val, bool) else str(val).lower() == 'true'
        if 'netbootxyz_url' in pxe_config:
            self.netbootxyz_url = str(pxe_config['netbootxyz_url']).strip() or self.netbootxyz_url
        if 'netbootxyz_efi_url' in pxe_config:
            self.netbootxyz_efi_url = str(pxe_config['netbootxyz_efi_url']).strip() or self.netbootxyz_efi_url

        if 'enabled_catalog_ids' in pxe_config:
            self.enabled_catalog_ids = list(pxe_config['enabled_catalog_ids'])

    def to_config_dict(self):
        """Return current settings as a dict suitable for config.json."""
        return {
            'base_dir': str(self.base_dir),
            'interface': self.interface,
            'dhcp_range': self.dhcp_range,
            'server_ip': self.server_ip,
            'http_port': self.http_port,
            'netbootxyz_enabled': self.netbootxyz_enabled,
            'netbootxyz_url': self.netbootxyz_url,
            'netbootxyz_efi_url': self.netbootxyz_efi_url,
            'enabled_catalog_ids': list(self.enabled_catalog_ids),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _human_size(nbytes):
        """Convert bytes to a human-readable string."""
        for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
            if abs(nbytes) < 1024.0:
                return f'{nbytes:.1f} {unit}'
            nbytes /= 1024.0
        return f'{nbytes:.1f} PB'

    @staticmethod
    def _detect_boot_type(path):
        """Heuristic: detect boot method based on file extension."""
        name = path.name.lower()
        if name.endswith('.iso'):
            return 'memdisk'   # syslinux memdisk for legacy BIOS ISO boot
        elif name.endswith('.efi'):
            return 'imgload'   # iPXE imgload+imgexec for UEFI images
        elif name.endswith('.img') or name.endswith('.raw'):
            return 'memdisk'   # raw disk images via memdisk
        return 'memdisk'

    @staticmethod
    def sanitize_filename(filename):
        """
        Sanitize an upload filename to prevent path traversal and
        filesystem issues.
        """
        import re as _re
        name = os.path.basename(filename)
        name = _re.sub(r'[^\w.\-]', '_', name)
        if not name or name.startswith('.'):
            name = 'image_' + name
        return name
