import os
import time
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class SystemMonitor:
    """
    Monitors the KVM host system's resources.

    Reads from /proc and /sys on Linux (Alpine) to avoid requiring
    psutil as a dependency. All methods return dicts and gracefully
    degrade if a source is unavailable.
    """

    def __init__(self):
        self._prev_cpu = None
        self._prev_cpu_time = None
        self._prev_net = None
        self._prev_net_time = None

    # ------------------------------------------------------------------
    # CPU
    # ------------------------------------------------------------------

    def get_cpu(self):
        """
        Return CPU usage percentage (across all cores) and core count.

        Uses /proc/stat delta between two calls; the first call returns
        0% because there is no previous sample.
        """
        try:
            with open('/proc/stat') as f:
                line = f.readline()  # "cpu  user nice system idle ..."
            parts = line.split()
            if parts[0] != 'cpu':
                return {'percent': 0, 'cores': self._core_count()}

            values = list(map(int, parts[1:]))
            idle = values[3] + (values[4] if len(values) > 4 else 0)  # idle + iowait
            total = sum(values)
            now = time.monotonic()

            usage = 0.0
            if self._prev_cpu is not None:
                d_total = total - self._prev_cpu[0]
                d_idle = idle - self._prev_cpu[1]
                if d_total > 0:
                    usage = round((1.0 - d_idle / d_total) * 100, 1)

            self._prev_cpu = (total, idle)
            self._prev_cpu_time = now

            return {'percent': usage, 'cores': self._core_count()}
        except Exception as e:
            logger.debug(f"CPU read error: {e}")
            return {'percent': 0, 'cores': 0}

    @staticmethod
    def _core_count():
        try:
            return os.cpu_count() or 0
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # Memory
    # ------------------------------------------------------------------

    @staticmethod
    def get_memory():
        """Return memory stats in MB from /proc/meminfo."""
        try:
            info = {}
            with open('/proc/meminfo') as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        info[parts[0].rstrip(':')] = int(parts[1])  # kB

            total = info.get('MemTotal', 0) / 1024
            available = info.get('MemAvailable', info.get('MemFree', 0)) / 1024
            used = total - available
            percent = round((used / total) * 100, 1) if total > 0 else 0

            return {
                'total_mb': round(total, 1),
                'used_mb': round(used, 1),
                'available_mb': round(available, 1),
                'percent': percent,
            }
        except Exception as e:
            logger.debug(f"Memory read error: {e}")
            return {'total_mb': 0, 'used_mb': 0, 'available_mb': 0, 'percent': 0}

    # ------------------------------------------------------------------
    # Disk
    # ------------------------------------------------------------------

    @staticmethod
    def get_disk(path='/'):
        """Return disk usage for the given mount point."""
        try:
            st = os.statvfs(path)
            total = st.f_blocks * st.f_frsize
            free = st.f_bavail * st.f_frsize
            used = total - free
            percent = round((used / total) * 100, 1) if total > 0 else 0

            def to_gb(b):
                return round(b / (1024 ** 3), 2)

            return {
                'total_gb': to_gb(total),
                'used_gb': to_gb(used),
                'free_gb': to_gb(free),
                'percent': percent,
                'mount': path,
            }
        except Exception as e:
            logger.debug(f"Disk read error: {e}")
            return {'total_gb': 0, 'used_gb': 0, 'free_gb': 0, 'percent': 0, 'mount': path}

    # ------------------------------------------------------------------
    # Temperature
    # ------------------------------------------------------------------

    @staticmethod
    def get_temperature():
        """
        Read CPU/SoC temperature from thermal zones.

        Returns list of {'zone': name, 'temp_c': float}.
        """
        temps = []
        thermal_base = Path('/sys/class/thermal')
        try:
            if not thermal_base.exists():
                return temps
            for zone in sorted(thermal_base.iterdir()):
                if not zone.name.startswith('thermal_zone'):
                    continue
                temp_file = zone / 'temp'
                type_file = zone / 'type'
                if temp_file.exists():
                    raw = int(temp_file.read_text().strip())
                    name = type_file.read_text().strip() if type_file.exists() else zone.name
                    temps.append({'zone': name, 'temp_c': round(raw / 1000.0, 1)})
        except Exception as e:
            logger.debug(f"Temperature read error: {e}")
        return temps

    # ------------------------------------------------------------------
    # Uptime
    # ------------------------------------------------------------------

    @staticmethod
    def get_uptime():
        """Return system uptime in seconds and human-readable string."""
        try:
            with open('/proc/uptime') as f:
                secs = float(f.read().split()[0])
            days = int(secs // 86400)
            hours = int((secs % 86400) // 3600)
            minutes = int((secs % 3600) // 60)
            human = ''
            if days:
                human += f'{days}d '
            human += f'{hours}h {minutes}m'
            return {'seconds': round(secs, 1), 'human': human.strip()}
        except Exception as e:
            logger.debug(f"Uptime read error: {e}")
            return {'seconds': 0, 'human': 'unknown'}

    # ------------------------------------------------------------------
    # Network
    # ------------------------------------------------------------------

    def get_network(self):
        """
        Return per-interface RX/TX bytes and current throughput.

        Throughput is computed as delta since the last call.
        """
        interfaces = {}
        try:
            with open('/proc/net/dev') as f:
                lines = f.readlines()[2:]  # skip headers
            now = time.monotonic()

            for line in lines:
                parts = line.split()
                iface = parts[0].rstrip(':')
                if iface == 'lo':
                    continue
                rx_bytes = int(parts[1])
                tx_bytes = int(parts[9])

                rx_rate = 0.0
                tx_rate = 0.0
                if self._prev_net and iface in self._prev_net:
                    dt = now - self._prev_net_time
                    if dt > 0:
                        rx_rate = (rx_bytes - self._prev_net[iface][0]) / dt
                        tx_rate = (tx_bytes - self._prev_net[iface][1]) / dt

                interfaces[iface] = {
                    'rx_bytes': rx_bytes,
                    'tx_bytes': tx_bytes,
                    'rx_rate_bps': round(rx_rate),
                    'tx_rate_bps': round(tx_rate),
                }

            # Store for next delta
            self._prev_net = {k: (v['rx_bytes'], v['tx_bytes']) for k, v in interfaces.items()}
            self._prev_net_time = now

        except Exception as e:
            logger.debug(f"Network read error: {e}")
        return interfaces

    # ------------------------------------------------------------------
    # Load average
    # ------------------------------------------------------------------

    @staticmethod
    def get_load():
        """Return 1/5/15 minute load averages."""
        try:
            with open('/proc/loadavg') as f:
                parts = f.read().split()
            return {
                'load_1': float(parts[0]),
                'load_5': float(parts[1]),
                'load_15': float(parts[2]),
            }
        except Exception as e:
            logger.debug(f"Load read error: {e}")
            return {'load_1': 0, 'load_5': 0, 'load_15': 0}

    # ------------------------------------------------------------------
    # Aggregate
    # ------------------------------------------------------------------

    def get_all(self):
        """Return all system metrics in one dict."""
        return {
            'cpu': self.get_cpu(),
            'memory': self.get_memory(),
            'disk': self.get_disk(),
            'temperature': self.get_temperature(),
            'uptime': self.get_uptime(),
            'network': self.get_network(),
            'load': self.get_load(),
        }
