import socket
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class WakeOnLANManager:
    """
    Wake-on-LAN (WOL) support for powering on machines remotely.
    Sends magic packets to broadcast MAC addresses.
    """
    
    def __init__(self):
        """Initialize WOL manager."""
        self.wol_targets = {}  # Store configured targets
    
    def add_target(self, name, mac_address, broadcast_ip='255.255.255.255', port=9):
        """
        Add a WOL target machine.
        
        Args:
            name: Target machine name (e.g., 'Server1')
            mac_address: MAC address in format 'AA:BB:CC:DD:EE:FF'
            broadcast_ip: Broadcast IP for WOL packet (default 255.255.255.255)
            port: UDP port for WOL (default 9)
        
        Returns:
            dict with status
        """
        try:
            # Validate MAC address format
            if not self._validate_mac(mac_address):
                return {'status': 'error', 'error': 'Invalid MAC address format'}
            
            self.wol_targets[name] = {
                'mac_address': mac_address,
                'broadcast_ip': broadcast_ip,
                'port': port,
                'added': datetime.utcnow().isoformat()
            }
            
            logger.info(f"WOL target added: {name} ({mac_address})")
            return {
                'status': 'ok',
                'message': f'Target {name} added'
            }
        except Exception as e:
            logger.error(f"Add WOL target failed: {e}")
            return {'status': 'error', 'error': str(e)}
    
    def remove_target(self, name):
        """Remove a WOL target."""
        if name in self.wol_targets:
            del self.wol_targets[name]
            logger.info(f"WOL target removed: {name}")
            return {'status': 'ok', 'message': f'Target {name} removed'}
        return {'status': 'error', 'error': 'Target not found'}
    
    def list_targets(self):
        """List all WOL targets."""
        return {
            'targets': list(self.wol_targets.keys()),
            'count': len(self.wol_targets),
            'details': self.wol_targets
        }
    
    def send_wol(self, name):
        """
        Send WOL magic packet to wake up target machine.
        
        Args:
            name: Target machine name
        
        Returns:
            dict with status
        """
        if name not in self.wol_targets:
            return {'status': 'error', 'error': 'Target not found'}
        
        target = self.wol_targets[name]
        
        try:
            magic_packet = self._create_magic_packet(target['mac_address'])
            self._send_packet(
                magic_packet,
                target['broadcast_ip'],
                target['port']
            )
            
            logger.info(f"WOL magic packet sent to {name} ({target['mac_address']})")
            return {
                'status': 'ok',
                'message': f'WOL packet sent to {name}',
                'target': name,
                'mac_address': target['mac_address'],
                'timestamp': datetime.utcnow().isoformat()
            }
        except Exception as e:
            logger.error(f"WOL send failed: {e}")
            return {
                'status': 'error',
                'error': str(e),
                'target': name
            }
    
    def send_wol_by_mac(self, mac_address, broadcast_ip='255.255.255.255', port=9):
        """
        Send WOL magic packet to any MAC address (without pre-registered target).
        
        Args:
            mac_address: MAC address in format 'AA:BB:CC:DD:EE:FF'
            broadcast_ip: Broadcast IP
            port: UDP port
        
        Returns:
            dict with status
        """
        try:
            if not self._validate_mac(mac_address):
                return {'status': 'error', 'error': 'Invalid MAC address format'}
            
            magic_packet = self._create_magic_packet(mac_address)
            self._send_packet(magic_packet, broadcast_ip, port)
            
            logger.info(f"WOL magic packet sent to {mac_address}")
            return {
                'status': 'ok',
                'message': f'WOL packet sent',
                'mac_address': mac_address,
                'broadcast_ip': broadcast_ip,
                'port': port,
                'timestamp': datetime.utcnow().isoformat()
            }
        except Exception as e:
            logger.error(f"WOL ad-hoc send failed: {e}")
            return {'status': 'error', 'error': str(e)}
    
    @staticmethod
    def _validate_mac(mac_address):
        """
        Validate MAC address format.
        
        Supported formats:
        - AA:BB:CC:DD:EE:FF (colon-separated)
        - AA-BB-CC-DD-EE-FF (hyphen-separated)
        - AABBCCDDEEFF (no separator)
        
        Returns:
            bool indicating if MAC is valid
        """
        import re
        
        mac_patterns = [
            r'^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$',  # Colon or hyphen
            r'^([0-9A-Fa-f]{12})$'  # No separator
        ]
        
        return any(re.match(pattern, mac_address) for pattern in mac_patterns)
    
    @staticmethod
    def _create_magic_packet(mac_address):
        """
        Create WOL magic packet.
        
        Magic packet format:
        - First 6 bytes: 0xFF (sync bytes)
        - Repeated 16 times: MAC address (6 bytes each)
        
        Args:
            mac_address: MAC address string
        
        Returns:
            bytes: Magic packet data
        """
        # Normalize MAC address to bytes
        mac_bytes = WakeOnLANManager._mac_to_bytes(mac_address)
        
        # Magic packet: 6 x 0xFF + 16 x MAC address
        magic_packet = b'\xff' * 6 + mac_bytes * 16
        
        return magic_packet
    
    @staticmethod
    def _mac_to_bytes(mac_address):
        """
        Convert MAC address string to bytes.
        
        Args:
            mac_address: MAC address in format AA:BB:CC:DD:EE:FF
        
        Returns:
            bytes: 6-byte MAC address
        """
        # Remove separators (colon or hyphen)
        mac_clean = mac_address.replace(':', '').replace('-', '')
        
        # Convert hex string to bytes
        return bytes.fromhex(mac_clean)
    
    @staticmethod
    def _send_packet(packet, broadcast_ip, port):
        """
        Send UDP packet to broadcast address.
        
        Args:
            packet: Packet bytes to send
            broadcast_ip: Broadcast IP address
            port: UDP port (default 9)
        
        Raises:
            Exception: If packet sending fails
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        try:
            # Set socket options for broadcast
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            
            # Send packet
            sock.sendto(packet, (broadcast_ip, port))
            
            logger.debug(f"WOL packet sent to {broadcast_ip}:{port} ({len(packet)} bytes)")
        finally:
            sock.close()
    
    @staticmethod
    def parse_mac_formats(mac_addresses_str):
        """
        Parse comma-separated list of MAC addresses.
        
        Args:
            mac_addresses_str: Comma-separated MAC addresses
                              (e.g., "AA:BB:CC:DD:EE:FF, 11:22:33:44:55:66")
        
        Returns:
            list: Valid MAC addresses
        """
        macs = []
        for mac in mac_addresses_str.split(','):
            mac = mac.strip()
            if WakeOnLANManager._validate_mac(mac):
                macs.append(mac)
        return macs


# Convenience function for quick WOL
def send_magic_packet(mac_address, broadcast_ip='255.255.255.255', port=9):
    """
    Convenience function to send WOL magic packet without manager.
    
    Args:
        mac_address: Target MAC address
        broadcast_ip: Broadcast IP
        port: UDP port
    
    Returns:
        bool: Success indicator
    """
    try:
        if not WakeOnLANManager._validate_mac(mac_address):
            logger.error(f"Invalid MAC address: {mac_address}")
            return False
        
        packet = WakeOnLANManager._create_magic_packet(mac_address)
        WakeOnLANManager._send_packet(packet, broadcast_ip, port)
        
        logger.info(f"Magic packet sent to {mac_address}")
        return True
    except Exception as e:
        logger.error(f"Magic packet failed: {e}")
        return False
