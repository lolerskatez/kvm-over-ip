"""
Hardware Video Encoder Detection and Selection.

Detects available hardware video encoders (VA-API, V4L2 M2M, NVENC)
and provides optimal encoder selection for low-latency WebRTC streaming.
"""

import logging
import subprocess
import platform
from typing import List, Dict, Optional
from pathlib import Path
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class EncoderInfo:
    """Information about a video encoder."""
    name: str
    codec: str  # 'h264', 'vp8', 'vp9'
    type: str  # 'hardware' or 'software'
    backend: str  # 'vaapi', 'v4l2m2m', 'nvenc', 'libvpx', 'x264', etc.
    priority: int  # Lower = higher priority
    device: Optional[str] = None  # Device path if hardware encoder


class EncoderDetector:
    """
    Detect and select optimal video encoders for WebRTC.
    
    Priority order:
    1. Hardware H.264 (VA-API, V4L2 M2M, NVENC)
    2. Hardware VP8 (VA-API)
    3. Software VP8 (libvpx)
    4. Software H.264 (libx264)
    5. MJPEG fallback
    """
    
    # Priority map: lower = better
    PRIORITY_MAP = {
        ('h264', 'hardware', 'vaapi'): 10,
        ('h264', 'hardware', 'v4l2m2m'): 11,
        ('h264', 'hardware', 'nvenc'): 12,
        ('vp8', 'hardware', 'vaapi'): 20,
        ('vp9', 'hardware', 'vaapi'): 21,
        ('vp8', 'software', 'libvpx'): 30,
        ('vp9', 'software', 'libvpx-vp9'): 31,
        ('h264', 'software', 'libx264'): 40,
        ('mjpeg', 'software', 'mjpeg'): 50,
    }
    
    def __init__(self):
        """Initialize encoder detector."""
        self.available_encoders: List[EncoderInfo] = []
        self._detected = False
    
    def detect(self) -> List[EncoderInfo]:
        """
        Detect all available encoders.
        
        Returns:
            List of EncoderInfo objects sorted by priority
        """
        if self._detected:
            return self.available_encoders
        
        logger.info("Detecting available video encoders...")
        
        self.available_encoders = []
        
        # Check for hardware encoders
        self._detect_vaapi()
        self._detect_v4l2_m2m()
        self._detect_nvenc()
        
        # Check for software encoders (always available if ffmpeg has them)
        self._detect_software_encoders()
        
        # Sort by priority
        self.available_encoders.sort(key=lambda e: e.priority)
        
        self._detected = True
        
        logger.info(f"Detected {len(self.available_encoders)} encoders")
        for enc in self.available_encoders:
            logger.info(
                f"  - {enc.name}: {enc.codec.upper()} "
                f"({enc.type}, {enc.backend}, priority={enc.priority})"
            )
        
        return self.available_encoders
    
    def get_best_encoder(self, codec: Optional[str] = None) -> Optional[EncoderInfo]:
        """
        Get the best available encoder, optionally filtered by codec.
        
        Args:
            codec: Optional codec filter ('h264', 'vp8', 'vp9', 'mjpeg')
        
        Returns:
            Best EncoderInfo or None if no encoders available
        """
        if not self._detected:
            self.detect()
        
        candidates = self.available_encoders
        
        if codec:
            candidates = [e for e in candidates if e.codec == codec.lower()]
        
        return candidates[0] if candidates else None
    
    def get_aiortc_encoder_name(self, encoder: EncoderInfo) -> str:
        """
        Convert EncoderInfo to aiortc encoder name.
        
        Args:
            encoder: EncoderInfo object
        
        Returns:
            Encoder name compatible with aiortc
        """
        if encoder.codec == 'h264':
            if encoder.backend == 'vaapi':
                return 'h264_vaapi'
            elif encoder.backend == 'v4l2m2m':
                return 'h264_v4l2m2m'
            elif encoder.backend == 'nvenc':
                return 'h264_nvenc'
            else:
                return 'libx264'
        elif encoder.codec == 'vp8':
            if encoder.backend == 'vaapi':
                return 'vp8_vaapi'
            else:
                return 'libvpx'
        elif encoder.codec == 'vp9':
            if encoder.backend == 'vaapi':
                return 'vp9_vaapi'
            else:
                return 'libvpx-vp9'
        else:
            return 'mjpeg'
    
    def _detect_vaapi(self):
        """Detect Intel VA-API hardware encoders."""
        # Check for VA-API device
        vaapi_devices = [
            '/dev/dri/renderD128',
            '/dev/dri/renderD129',
        ]
        
        vaapi_device = None
        for dev in vaapi_devices:
            if Path(dev).exists():
                vaapi_device = dev
                break
        
        if not vaapi_device:
            logger.debug("No VA-API device found")
            return
        
        # Check if vainfo works
        try:
            result = subprocess.run(
                ['vainfo', '--display', 'drm', '--device', vaapi_device],
                capture_output=True,
                timeout=5,
                text=True
            )
            
            if result.returncode == 0:
                output = result.stdout + result.stderr
                
                # Check for H.264 encoding support
                if 'VAProfileH264' in output and 'VAEntrypointEncSlice' in output:
                    self.available_encoders.append(EncoderInfo(
                        name='VA-API H.264',
                        codec='h264',
                        type='hardware',
                        backend='vaapi',
                        priority=self.PRIORITY_MAP[('h264', 'hardware', 'vaapi')],
                        device=vaapi_device
                    ))
                    logger.debug(f"Detected VA-API H.264 encoder on {vaapi_device}")
                
                # Check for VP8 encoding support
                if 'VAProfileVP8' in output and 'VAEntrypointEncSlice' in output:
                    self.available_encoders.append(EncoderInfo(
                        name='VA-API VP8',
                        codec='vp8',
                        type='hardware',
                        backend='vaapi',
                        priority=self.PRIORITY_MAP[('vp8', 'hardware', 'vaapi')],
                        device=vaapi_device
                    ))
                    logger.debug(f"Detected VA-API VP8 encoder on {vaapi_device}")
                
                # Check for VP9 encoding support
                if 'VAProfileVP9' in output and 'VAEntrypointEncSlice' in output:
                    self.available_encoders.append(EncoderInfo(
                        name='VA-API VP9',
                        codec='vp9',
                        type='hardware',
                        backend='vaapi',
                        priority=self.PRIORITY_MAP[('vp9', 'hardware', 'vaapi')],
                        device=vaapi_device
                    ))
                    logger.debug(f"Detected VA-API VP9 encoder on {vaapi_device}")
        
        except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError):
            logger.debug("VA-API detection failed")
    
    def _detect_v4l2_m2m(self):
        """Detect V4L2 Memory-to-Memory hardware encoders (Raspberry Pi)."""
        # Check for V4L2 encoder devices
        encoder_devices = []
        
        # Raspberry Pi typically uses /dev/video11 for H.264 encoder
        for i in range(10, 20):
            dev_path = f'/dev/video{i}'
            if Path(dev_path).exists():
                try:
                    # Try to read device capabilities
                    result = subprocess.run(
                        ['v4l2-ctl', '--device', dev_path, '--all'],
                        capture_output=True,
                        timeout=2,
                        text=True
                    )
                    
                    if result.returncode == 0:
                        output = result.stdout
                        
                        # Check if it's an encoder (has VIDEO_M2M_OUTPUT capability)
                        if 'Video Memory-to-Memory' in output or 'Encoder' in output:
                            if 'H264' in output or 'H.264' in output:
                                encoder_devices.append((dev_path, 'h264'))
                            elif 'VP8' in output:
                                encoder_devices.append((dev_path, 'vp8'))
                
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    continue
        
        # Add detected encoders
        for dev_path, codec in encoder_devices:
            self.available_encoders.append(EncoderInfo(
                name=f'V4L2 M2M {codec.upper()}',
                codec=codec,
                type='hardware',
                backend='v4l2m2m',
                priority=self.PRIORITY_MAP[(codec, 'hardware', 'v4l2m2m')],
                device=dev_path
            ))
            logger.debug(f"Detected V4L2 M2M {codec.upper()} encoder on {dev_path}")
    
    def _detect_nvenc(self):
        """Detect NVIDIA NVENC hardware encoders."""
        # Check for NVIDIA GPU
        try:
            result = subprocess.run(
                ['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
                capture_output=True,
                timeout=5,
                text=True
            )
            
            if result.returncode == 0 and result.stdout.strip():
                # NVIDIA GPU present - assume NVENC available
                self.available_encoders.append(EncoderInfo(
                    name='NVIDIA NVENC H.264',
                    codec='h264',
                    type='hardware',
                    backend='nvenc',
                    priority=self.PRIORITY_MAP[('h264', 'hardware', 'nvenc')],
                    device=None
                ))
                logger.debug("Detected NVIDIA NVENC H.264 encoder")
        
        except (subprocess.TimeoutExpired, FileNotFoundError):
            logger.debug("NVENC detection failed (nvidia-smi not found)")
    
    def _detect_software_encoders(self):
        """Detect software encoders via ffmpeg."""
        try:
            result = subprocess.run(
                ['ffmpeg', '-encoders'],
                capture_output=True,
                timeout=5,
                text=True
            )
            
            if result.returncode == 0:
                output = result.stdout
                
                # Check for libvpx (VP8)
                if 'libvpx' in output and ' vp8 ' in output:
                    self.available_encoders.append(EncoderInfo(
                        name='libvpx VP8',
                        codec='vp8',
                        type='software',
                        backend='libvpx',
                        priority=self.PRIORITY_MAP[('vp8', 'software', 'libvpx')],
                        device=None
                    ))
                    logger.debug("Detected libvpx VP8 software encoder")
                
                # Check for libvpx-vp9 (VP9)
                if 'libvpx-vp9' in output:
                    self.available_encoders.append(EncoderInfo(
                        name='libvpx-vp9 VP9',
                        codec='vp9',
                        type='software',
                        backend='libvpx-vp9',
                        priority=self.PRIORITY_MAP[('vp9', 'software', 'libvpx-vp9')],
                        device=None
                    ))
                    logger.debug("Detected libvpx-vp9 VP9 software encoder")
                
                # Check for libx264 (H.264)
                if 'libx264' in output:
                    self.available_encoders.append(EncoderInfo(
                        name='libx264 H.264',
                        codec='h264',
                        type='software',
                        backend='libx264',
                        priority=self.PRIORITY_MAP[('h264', 'software', 'libx264')],
                        device=None
                    ))
                    logger.debug("Detected libx264 H.264 software encoder")
                
                # MJPEG is always available as fallback
                self.available_encoders.append(EncoderInfo(
                    name='MJPEG',
                    codec='mjpeg',
                    type='software',
                    backend='mjpeg',
                    priority=self.PRIORITY_MAP[('mjpeg', 'software', 'mjpeg')],
                    device=None
                ))
                logger.debug("MJPEG fallback encoder available")
        
        except (subprocess.TimeoutExpired, FileNotFoundError):
            logger.warning("Could not detect software encoders (ffmpeg not found)")
    
    def get_encoder_config(self, encoder: EncoderInfo) -> dict:
        """
        Get recommended configuration for an encoder.
        
        Args:
            encoder: EncoderInfo object
        
        Returns:
            Dictionary with recommended parameters
        """
        config = {
            'encoder_name': self.get_aiortc_encoder_name(encoder),
            'codec': encoder.codec,
            'type': encoder.type,
        }
        
        # Codec-specific defaults
        if encoder.codec == 'h264':
            config.update({
                'profile': 'baseline',  # Best browser compatibility
                'preset': 'ultrafast' if encoder.type == 'software' else 'llhp',
                'tune': 'zerolatency',
                'keyframe_interval': 15,  # I-frame every 15 frames for low latency
            })
        elif encoder.codec in ('vp8', 'vp9'):
            config.update({
                'deadline': 'realtime',
                'cpu_used': 8,  # Fastest encoding
                'keyframe_interval': 15,
            })
        
        # Hardware-specific settings
        if encoder.backend == 'vaapi' and encoder.device:
            config['vaapi_device'] = encoder.device
        elif encoder.backend == 'v4l2m2m' and encoder.device:
            config['v4l2_device'] = encoder.device
        
        return config
