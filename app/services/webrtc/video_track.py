"""
WebRTC Video Track from V4L2 Capture.

Provides a MediaStreamTrack that captures frames from V4L2 devices
and encodes them for WebRTC transmission with minimal latency.
"""

import asyncio
import logging
import time
from typing import Optional
from fractions import Fraction

try:
    from aiortc import MediaStreamTrack, VideoStreamTrack
    from aiortc.mediastreams import VideoFrame
    from av import VideoFrame as AVVideoFrame
    import av
    import numpy as np
    AIORTC_AVAILABLE = True
except ImportError:
    AIORTC_AVAILABLE = False
    MediaStreamTrack = object
    logging.warning("aiortc not available - WebRTC video track disabled")

from app.services.video.v4l2_capture import V4L2Capture
from app.services.video.encoder_detection import EncoderDetector, EncoderInfo

logger = logging.getLogger(__name__)


class V4L2VideoTrack(VideoStreamTrack):
    """
    WebRTC video track sourced from V4L2 capture device.
    
    Captures frames from V4L2 device and provides them as a WebRTC
    MediaStreamTrack for transmission via RTCPeerConnection.
    
    Supports hardware-accelerated encoding for minimal latency.
    """
    
    kind = "video"
    
    def __init__(
        self,
        device_path: str = '/dev/video0',
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
        codec: str = 'h264',
        prefer_hardware: bool = True
    ):
        """
        Initialize V4L2 video track.
        
        Args:
            device_path: Path to V4L2 device
            width: Video width in pixels
            height: Video height in pixels
            fps: Target frames per second
            codec: Preferred codec ('h264', 'vp8', 'vp9')
            prefer_hardware: Prefer hardware encoders if available
        """
        super().__init__()
        
        if not AIORTC_AVAILABLE:
            raise RuntimeError("aiortc module not available")
        
        self.device_path = device_path
        self.width = width
        self.height = height
        self.fps = fps
        self.codec = codec.lower()
        self.prefer_hardware = prefer_hardware
        
        # V4L2 capture
        self.capture: Optional[V4L2Capture] = None
        self.frame_queue = asyncio.Queue(maxsize=2)  # Small queue for low latency
        
        # Encoder selection
        self.encoder_detector = EncoderDetector()
        self.selected_encoder: Optional[EncoderInfo] = None
        
        # Frame timing
        self.frame_time = 1 / fps  # Seconds per frame
        self.next_frame_time = 0
        
        # Statistics
        self.frames_sent = 0
        self.frames_dropped = 0
        self.avg_encode_time_ms = 0.0
        
        self._started = False
    
    async def _initialize(self):
        """Initialize V4L2 capture and encoder (async)."""
        if self._started:
            return
        
        # Detect encoders
        logger.info("Detecting video encoders...")
        encoders = self.encoder_detector.detect()
        
        if not encoders:
            raise RuntimeError("No video encoders available")
        
        # Select best encoder for requested codec
        if self.prefer_hardware:
            # Try hardware first
            self.selected_encoder = self.encoder_detector.get_best_encoder(self.codec)
            if not self.selected_encoder or self.selected_encoder.type != 'hardware':
                # Fall back to any encoder for this codec
                self.selected_encoder = self.encoder_detector.get_best_encoder(self.codec)
        else:
            self.selected_encoder = self.encoder_detector.get_best_encoder(self.codec)
        
        if not self.selected_encoder:
            # No encoder for requested codec - use best available
            self.selected_encoder = self.encoder_detector.get_best_encoder()
            logger.warning(
                f"No {self.codec} encoder found, using {self.selected_encoder.name}"
            )
        
        logger.info(
            f"Selected encoder: {self.selected_encoder.name} "
            f"({self.selected_encoder.codec}, {self.selected_encoder.type})"
        )
        
        # Initialize V4L2 capture
        # Use MJPEG format from device if available (reduces USB bandwidth)
        self.capture = V4L2Capture(
            device_path=self.device_path,
            width=self.width,
            height=self.height,
            fps=self.fps,
            pixel_format='MJPEG',
            buffer_count=2  # Minimal latency
        )
        
        if not self.capture.open():
            raise RuntimeError(f"Failed to open V4L2 device: {self.device_path}")
        
        # Set frame callback
        self.capture.set_frame_callback(self._on_frame)
        
        # Start capture
        if not self.capture.start():
            raise RuntimeError("Failed to start V4L2 capture")
        
        self._started = True
        logger.info(f"V4L2 video track initialized: {self.width}x{self.height}@{self.fps}fps")
    
    def _on_frame(self, frame_data: bytes, timestamp: float):
        """
        Callback when V4L2 frame is captured.
        
        Args:
            frame_data: Raw frame data (MJPEG or YUYV)
            timestamp: V4L2 timestamp in seconds
        """
        try:
            # Add frame to queue (non-blocking)
            # If queue is full, drop the oldest frame
            if self.frame_queue.full():
                try:
                    self.frame_queue.get_nowait()
                    self.frames_dropped += 1
                except asyncio.QueueEmpty:
                    pass
            
            self.frame_queue.put_nowait((frame_data, timestamp))
        
        except Exception as e:
            logger.error(f"Error queuing frame: {e}")
    
    async def recv(self) -> AVVideoFrame:
        """
        Receive next video frame for WebRTC transmission.
        
        Called by aiortc to get frames for encoding and transmission.
        
        Returns:
            VideoFrame object
        """
        if not self._started:
            await self._initialize()
        
        # Wait for next frame
        encode_start = time.monotonic()
        
        try:
            # Get frame from queue with timeout
            frame_data, v4l2_timestamp = await asyncio.wait_for(
                self.frame_queue.get(),
                timeout=2.0
            )
        except asyncio.TimeoutError:
            logger.warning("Frame receive timeout - V4L2 capture may have stopped")
            # Return a blank frame to prevent connection drop
            return self._create_blank_frame()
        
        try:
            # Decode MJPEG to RGB
            # Use av library for efficient decode
            packet = av.Packet(frame_data)
            codec_context = av.CodecContext.create('mjpeg', 'r')
            
            frames = codec_context.decode(packet)
            if frames:
                av_frame = frames[0]
                
                # Convert to RGB if needed
                if av_frame.format.name != 'rgb24':
                    av_frame = av_frame.reformat(format='rgb24')
                
                # Set presentation timestamp for WebRTC
                # Use monotonic time for RTP timestamp calculation
                pts = int(self.next_frame_time * self.fps)
                av_frame.pts = pts
                av_frame.time_base = Fraction(1, self.fps)
                
                self.next_frame_time += self.frame_time
                self.frames_sent += 1
                
                # Update encode time statistics
                encode_time_ms = (time.monotonic() - encode_start) * 1000
                alpha = 0.1
                self.avg_encode_time_ms = (
                    alpha * encode_time_ms +
                    (1 - alpha) * self.avg_encode_time_ms
                )
                
                return av_frame
            else:
                logger.warning("Failed to decode MJPEG frame")
                return self._create_blank_frame()
        
        except Exception as e:
            logger.error(f"Error decoding frame: {e}")
            return self._create_blank_frame()
    
    def _create_blank_frame(self) -> AVVideoFrame:
        """
        Create a blank video frame for error recovery.
        
        Returns:
            Black VideoFrame
        """
        # Create blank RGB frame
        blank = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        av_frame = AVVideoFrame.from_ndarray(blank, format='rgb24')
        
        pts = int(self.next_frame_time * self.fps)
        av_frame.pts = pts
        av_frame.time_base = Fraction(1, self.fps)
        
        self.next_frame_time += self.frame_time
        
        return av_frame
    
    async def stop(self):
        """Stop the video track and release resources."""
        if self.capture:
            self.capture.close()
            self.capture = None
        
        self._started = False
        logger.info("V4L2 video track stopped")
    
    def get_stats(self) -> dict:
        """
        Get track statistics.
        
        Returns:
            Dictionary with frame counts and timing info
        """
        stats = {
            'frames_sent': self.frames_sent,
            'frames_dropped': self.frames_dropped,
            'avg_encode_time_ms': round(self.avg_encode_time_ms, 2),
            'device': self.device_path,
            'resolution': f"{self.width}x{self.height}",
            'fps': self.fps,
            'codec': self.codec,
        }
        
        if self.selected_encoder:
            stats['encoder'] = {
                'name': self.selected_encoder.name,
                'type': self.selected_encoder.type,
                'backend': self.selected_encoder.backend,
            }
        
        if self.capture:
            stats['v4l2'] = self.capture.get_stats()
        
        return stats


class StaticImageTrack(VideoStreamTrack):
    """
    WebRTC video track that sends a static test pattern.
    
    Useful for testing WebRTC connection without V4L2 hardware.
    """
    
    kind = "video"
    
    def __init__(self, width: int = 640, height: int = 480, fps: int = 30):
        """
        Initialize static image track.
        
        Args:
            width: Image width
            height: Image height
            fps: Target frames per second
        """
        super().__init__()
        self.width = width
        self.height = height
        self.fps = fps
        self.frame_time = 1 / fps
        self.next_frame_time = 0
        self.frame_count = 0
    
    async def recv(self) -> AVVideoFrame:
        """
        Receive next video frame.
        
        Returns:
            VideoFrame with test pattern
        """
        # Wait for next frame time
        await asyncio.sleep(self.frame_time)
        
        # Create test pattern (color bars)
        img = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        
        # Color bars
        bar_width = self.width // 7
        colors = [
            (255, 255, 255),  # White
            (255, 255, 0),    # Yellow
            (0, 255, 255),    # Cyan
            (0, 255, 0),      # Green
            (255, 0, 255),    # Magenta
            (255, 0, 0),      # Red
            (0, 0, 255),      # Blue
        ]
        
        for i, color in enumerate(colors):
            x_start = i * bar_width
            x_end = min(x_start + bar_width, self.width)
            img[:, x_start:x_end] = color
        
        # Add frame counter
        text_y = self.height // 2
        text = f"Frame {self.frame_count}"
        # Note: Would need cv2.putText() for actual text rendering
        # For now, just increment counter
        
        av_frame = AVVideoFrame.from_ndarray(img, format='rgb24')
        
        pts = int(self.next_frame_time * self.fps)
        av_frame.pts = pts
        av_frame.time_base = Fraction(1, self.fps)
        
        self.next_frame_time += self.frame_time
        self.frame_count += 1
        
        return av_frame
