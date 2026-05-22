import subprocess
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Pre-built EDID binaries for common resolutions.
# These are standard 128-byte base EDID blocks that advertise a single
# preferred timing so the target GPU always outputs a known resolution.
# Hex-encoded; decode with bytes.fromhex().
EDID_PRESETS = {
    '1920x1080@60': {
        'label': '1920x1080 @ 60 Hz (Full HD)',
        'hex': (
            '00ffffffffffff001ab30000000000000117010380301b78'
            '0aee91a3544c99260f5054000000010101010101010101'
            '010101010101023a801871382d40582c4500dd0c1100001e'
            '0000000000000000000000000000000000000000'
            '00fe004b564d2d6f7665722d4950000000000010'
            '00000000000000000000000000000000c8'
        ),
    },
    '1280x720@60': {
        'label': '1280x720 @ 60 Hz (HD)',
        'hex': (
            '00ffffffffffff001ab30000000000000117010380301b78'
            '0aee91a3544c99260f5054000000010101010101010101'
            '0101010101010101011d007251d01e206e285500dd0c1100001e'
            '0000000000000000000000000000000000'
            '00fe004b564d2d6f7665722d4950000000000010'
            '00000000000000000000000000000000b0'
        ),
    },
    '1024x768@60': {
        'label': '1024x768 @ 60 Hz (XGA)',
        'hex': (
            '00ffffffffffff001ab30000000000000117010380301b78'
            '0aee91a3544c99260f5054000000010101010101010101'
            '01010101010101006421004060410028400890360000001e'
            '0000000000000000000000000000000000'
            '00fe004b564d2d6f7665722d4950000000000010'
            '00000000000000000000000000000000a6'
        ),
    },
}


class EDIDManager:
    """
    Manages EDID emulation for the video capture device.

    On Linux, some USB capture cards (and the video4linux subsystem)
    allow writing a custom EDID via sysfs so the target machine's GPU
    sees a specific display and outputs a known resolution.

    Typical sysfs path:
        /sys/class/video4linux/video0/edid

    This requires write access to sysfs (root or appropriate udev rule).
    """

    def __init__(self, video_device='/dev/video0'):
        """
        Initialize EDID manager.

        Args:
            video_device: Path to the V4L2 video device.
        """
        self.video_device = video_device
        self._device_name = Path(video_device).name  # e.g. "video0"
        self._edid_path = Path(f'/sys/class/video4linux/{self._device_name}/edid')

    @property
    def edid_sysfs_path(self):
        return self._edid_path

    def is_supported(self):
        """Check if the capture device supports EDID writing."""
        return self._edid_path.exists()

    def get_current_edid(self):
        """
        Read the current EDID from the capture device.

        Returns:
            Hex string of the current EDID, or None.
        """
        if not self._edid_path.exists():
            return None
        try:
            raw = self._edid_path.read_bytes()
            if not raw or raw == b'\x00' * len(raw):
                return None
            return raw.hex()
        except Exception as e:
            logger.error(f"Failed to read EDID: {e}")
            return None

    def write_edid(self, edid_hex):
        """
        Write an EDID to the capture device via sysfs.

        Args:
            edid_hex: Hex-encoded EDID data (256 or 128 chars minimum).

        Returns:
            True on success, error string on failure.
        """
        if not self._edid_path.exists():
            return 'EDID sysfs path not found. Device may not support EDID emulation.'

        try:
            edid_bytes = bytes.fromhex(edid_hex)
        except ValueError:
            return 'Invalid hex data'

        if len(edid_bytes) < 128:
            return 'EDID must be at least 128 bytes'

        try:
            self._edid_path.write_bytes(edid_bytes)
            logger.info(f"EDID written to {self._edid_path} ({len(edid_bytes)} bytes)")
            return True
        except PermissionError:
            return 'Permission denied. Run as root or add a udev rule granting write access.'
        except Exception as e:
            return str(e)

    def write_preset(self, preset_name):
        """
        Write a pre-built EDID preset.

        Args:
            preset_name: Key from EDID_PRESETS (e.g. '1920x1080@60').

        Returns:
            True on success, error string on failure.
        """
        preset = EDID_PRESETS.get(preset_name)
        if not preset:
            return f'Unknown preset: {preset_name}'
        return self.write_edid(preset['hex'])

    def clear_edid(self):
        """
        Clear/reset the EDID (write zeros).

        Returns:
            True on success, error string on failure.
        """
        if not self._edid_path.exists():
            return 'EDID sysfs path not found'
        try:
            self._edid_path.write_bytes(b'\x00' * 128)
            logger.info(f"EDID cleared on {self._edid_path}")
            return True
        except PermissionError:
            return 'Permission denied'
        except Exception as e:
            return str(e)

    def get_status(self):
        """
        Get EDID subsystem status.

        Returns:
            Dict with support status, current EDID info, and presets.
        """
        supported = self.is_supported()
        current = self.get_current_edid() if supported else None

        return {
            'supported': supported,
            'edid_path': str(self._edid_path),
            'video_device': self.video_device,
            'current_edid': current,
            'current_length': len(bytes.fromhex(current)) if current else 0,
            'presets': {k: v['label'] for k, v in EDID_PRESETS.items()},
        }

    def upload_edid(self, file_data):
        """
        Upload a raw EDID binary file.

        Args:
            file_data: Raw bytes of the EDID file.

        Returns:
            True on success, error string on failure.
        """
        if len(file_data) < 128:
            return 'EDID file too small (minimum 128 bytes)'
        if len(file_data) > 512:
            return 'EDID file too large (maximum 512 bytes)'
        return self.write_edid(file_data.hex())
