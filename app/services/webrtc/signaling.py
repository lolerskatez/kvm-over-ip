"""
WebRTC Signaling Server.

Handles SDP offer/answer exchange and ICE candidate negotiation
for establishing WebRTC peer connections.
"""

import asyncio
import json
import logging
import uuid
from typing import Dict, Optional, Callable
from dataclasses import dataclass, asdict
from datetime import datetime

try:
    from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate
    from aiortc.contrib.media import MediaRecorder
    AIORTC_AVAILABLE = True
except ImportError:
    AIORTC_AVAILABLE = False
    RTCPeerConnection = object
    logging.warning("aiortc not available - WebRTC signaling disabled")

from app.services.webrtc.video_track import V4L2VideoTrack
from app.services.webrtc.data_channel import HIDDataChannel

logger = logging.getLogger(__name__)


@dataclass
class PeerConnectionInfo:
    """Information about an active peer connection."""
    id: str
    created_at: datetime
    username: str
    client_ip: str
    state: str  # 'connecting', 'connected', 'failed', 'closed'
    video_track: Optional[str] = None
    data_channels: list = None


class WebRTCSignalingServer:
    """
    WebRTC signaling server for KVM control.
    
    Manages RTCPeerConnections, handles SDP negotiation, and coordinates
    video tracks and data channels.
    
    Supports single active connection (enforced by session management).
    """
    
    def __init__(self, hid_controller=None):
        """
        Initialize signaling server.
        
        Args:
            hid_controller: HID controller instance for input handling
        """
        if not AIORTC_AVAILABLE:
            raise RuntimeError("aiortc module not available")
        
        self.hid_controller = hid_controller
        self.peer_connections: Dict[str, RTCPeerConnection] = {}
        self.peer_info: Dict[str, PeerConnectionInfo] = {}
        self.active_tracks: Dict[str, V4L2VideoTrack] = {}
        self.data_channels: Dict[str, HIDDataChannel] = {}
        
        # Configuration
        self.config = {
            'video_device': '/dev/video0',
            'video_width': 1280,
            'video_height': 720,
            'video_fps': 30,
            'video_codec': 'h264',
            'prefer_hardware_encoding': True,
        }
        
        # Statistics
        self.total_connections = 0
        self.failed_connections = 0
        
    def update_config(self, config: dict):
        """
        Update server configuration.
        
        Args:
            config: Configuration dictionary
        """
        self.config.update(config)
        logger.info(f"WebRTC config updated: {config}")
    
    async def create_peer_connection(
        self,
        username: str,
        client_ip: str
    ) -> str:
        """
        Create a new RTCPeerConnection.
        
        Args:
            username: User creating the connection
            client_ip: Client IP address
        
        Returns:
            Peer connection ID (UUID)
        """
        peer_id = str(uuid.uuid4())
        
        # Create RTCPeerConnection
        pc = RTCPeerConnection()
        
        # Create peer info
        info = PeerConnectionInfo(
            id=peer_id,
            created_at=datetime.now(),
            username=username,
            client_ip=client_ip,
            state='connecting',
            data_channels=[]
        )
        
        # Store peer connection
        self.peer_connections[peer_id] = pc
        self.peer_info[peer_id] = info
        self.total_connections += 1
        
        # Add video track
        video_track = V4L2VideoTrack(
            device_path=self.config['video_device'],
            width=self.config['video_width'],
            height=self.config['video_height'],
            fps=self.config['video_fps'],
            codec=self.config['video_codec'],
            prefer_hardware=self.config['prefer_hardware_encoding']
        )
        
        pc.addTrack(video_track)
        self.active_tracks[peer_id] = video_track
        info.video_track = f"{self.config['video_width']}x{self.config['video_height']}@{self.config['video_fps']}fps"
        
        # Create data channel for HID input
        data_channel = pc.createDataChannel('hid-input')
        hid_channel = HIDDataChannel(data_channel, self.hid_controller)
        self.data_channels[peer_id] = hid_channel
        info.data_channels.append('hid-input')
        
        # Set up event handlers
        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            logger.info(f"Peer {peer_id[:8]} connection state: {pc.connectionState}")
            info.state = pc.connectionState
            
            if pc.connectionState == "failed":
                self.failed_connections += 1
                await self.close_peer_connection(peer_id)
            elif pc.connectionState == "closed":
                await self.close_peer_connection(peer_id)
        
        @pc.on("iceconnectionstatechange")
        async def on_iceconnectionstatechange():
            logger.debug(f"Peer {peer_id[:8]} ICE state: {pc.iceConnectionState}")
        
        logger.info(
            f"Created peer connection {peer_id[:8]} for {username} from {client_ip}"
        )
        
        return peer_id
    
    async def handle_offer(
        self,
        peer_id: str,
        offer_sdp: str,
        offer_type: str = 'offer'
    ) -> dict:
        """
        Handle SDP offer from client.
        
        Args:
            peer_id: Peer connection ID
            offer_sdp: SDP offer string
            offer_type: SDP type (usually 'offer')
        
        Returns:
            Dictionary with answer SDP
        """
        if peer_id not in self.peer_connections:
            raise ValueError(f"Peer connection {peer_id} not found")
        
        pc = self.peer_connections[peer_id]
        
        # Create RTCSessionDescription from offer
        offer = RTCSessionDescription(sdp=offer_sdp, type=offer_type)
        
        # Set remote description
        await pc.setRemoteDescription(offer)
        
        # Create answer
        answer = await pc.createAnswer()
        
        # Set local description
        await pc.setLocalDescription(answer)
        
        logger.info(f"Processed SDP offer/answer for peer {peer_id[:8]}")
        
        return {
            'sdp': pc.localDescription.sdp,
            'type': pc.localDescription.type
        }
    
    async def handle_ice_candidate(
        self,
        peer_id: str,
        candidate: dict
    ):
        """
        Handle ICE candidate from client.
        
        Args:
            peer_id: Peer connection ID
            candidate: ICE candidate dictionary
        """
        if peer_id not in self.peer_connections:
            raise ValueError(f"Peer connection {peer_id} not found")
        
        pc = self.peer_connections[peer_id]
        
        # Create RTCIceCandidate
        ice_candidate = RTCIceCandidate(
            candidate=candidate.get('candidate'),
            sdpMid=candidate.get('sdpMid'),
            sdpMLineIndex=candidate.get('sdpMLineIndex')
        )
        
        # Add ICE candidate
        await pc.addIceCandidate(ice_candidate)
        
        logger.debug(f"Added ICE candidate for peer {peer_id[:8]}")
    
    async def close_peer_connection(self, peer_id: str):
        """
        Close peer connection and clean up resources.
        
        Args:
            peer_id: Peer connection ID
        """
        if peer_id not in self.peer_connections:
            return
        
        logger.info(f"Closing peer connection {peer_id[:8]}")
        
        # Close data channel
        if peer_id in self.data_channels:
            del self.data_channels[peer_id]
        
        # Stop video track
        if peer_id in self.active_tracks:
            track = self.active_tracks[peer_id]
            await track.stop()
            del self.active_tracks[peer_id]
        
        # Close peer connection
        pc = self.peer_connections[peer_id]
        await pc.close()
        
        # Remove from tracking
        del self.peer_connections[peer_id]
        if peer_id in self.peer_info:
            del self.peer_info[peer_id]
        
        logger.info(f"Peer connection {peer_id[:8]} closed")
    
    async def close_all_connections(self):
        """Close all active peer connections."""
        peer_ids = list(self.peer_connections.keys())
        
        for peer_id in peer_ids:
            await self.close_peer_connection(peer_id)
        
        logger.info("All peer connections closed")
    
    def get_peer_info(self, peer_id: str) -> Optional[dict]:
        """
        Get information about a peer connection.
        
        Args:
            peer_id: Peer connection ID
        
        Returns:
            Peer info dictionary or None
        """
        if peer_id not in self.peer_info:
            return None
        
        info = self.peer_info[peer_id]
        result = asdict(info)
        result['created_at'] = info.created_at.isoformat()
        
        # Add statistics
        if peer_id in self.active_tracks:
            result['video_stats'] = self.active_tracks[peer_id].get_stats()
        
        return result
    
    def get_all_peers(self) -> list:
        """
        Get information about all active peers.
        
        Returns:
            List of peer info dictionaries
        """
        return [self.get_peer_info(peer_id) for peer_id in self.peer_connections]
    
    def get_stats(self) -> dict:
        """
        Get server statistics.
        
        Returns:
            Statistics dictionary
        """
        return {
            'active_connections': len(self.peer_connections),
            'total_connections': self.total_connections,
            'failed_connections': self.failed_connections,
            'peers': self.get_all_peers()
        }
