"""
V4L2 Direct Capture with Zero-Copy Memory Mapping.

Provides low-latency video frame capture from V4L2 devices using mmap()
for zero-copy buffer access. Optimized for WebRTC streaming with minimal
glass-to-glass latency.
"""

import logging
import time
import threading
from typing import Optional, Callable, Tuple
from pathlib import Path
import numpy as np

try:
    import v4l2py
    from v4l2py.device import Device, BufferType, PixelFormat
    V4L2_AVAILABLE = True
except ImportError:
    V4L2_AVAILABLE = False
    logging.warning("v4l2py not available - V4L2 direct capture disabled")

logger = logging.getLogger(__name__)


class V4L2Capture:
    """
    Direct V4L2 video capture using memory-mapped buffers.
    
    Uses VIDIOC_DQBUF/VIDIOC_QBUF for low-latency frame access without
    copying data through user space pipes. Supports MJPEG and YUYV formats.
    
    Attributes:
        device_path: Path to V4L2 device (e.g., /dev/video0)
        width: Frame width in pixels
        height: Frame height in pixels
        fps: Target frames per second
        pixel_format: V4L2 pixel format (MJPEG or YUYV)
        buffer_count: Number of buffers for ring queue (2-4 for low latency)
    """
    
    def __init__(
        self,
        device_path: str = '/dev/video0',
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
        pixel_format: str = 'MJPEG',
        buffer_count: int = 3
    ):
        """
        Initialize V4L2 capture device.
        
        Args:
            device_path: Path to video device
            width: Desired frame width
            height: Desired frame height
            fps: Target frames per second
            pixel_format: Pixel format ('MJPEG' or 'YUYV')
            buffer_count: Number of mmap buffers (2-4 recommended for low latency)
        """
        if not V4L2_AVAILABLE:
            raise RuntimeError("v4l2py module not available")
        
        self.device_path = device_path
        self.width = width
        self.height = height
        self.fps = fps
        self.pixel_format = pixel_format.upper()
        self.buffer_count = max(2, min(buffer_count, 4))  # Clamp 2-4
        
        self.device: Optional[Device] = None
        self.running = False
        self.capture_thread: Optional[threading.Thread] = None
        self.frame_callback: Optional[Callable[[bytes, float], None]] = None
        self.lock = threading.Lock()
        
        # Statistics
        self.frame_count = 0
        self.dropped_frames = 0
        self.avg_capture_time_ms = 0.0
        self.last_timestamp = 0.0
        
    def open(self) -> bool:
        """
        Open V4L2 device and configure capture parameters.
        
        Returns:
            True if successful, False otherwise
        """
        if not Path(self.device_path).exists():
            logger.error(f"V4L2 device not found: {self.device_path}")
            return False
        
        try:
            self.device = Device(self.device_path)
            
            # Determine pixel format
            if self.pixel_format == 'MJPEG':
                fmt = PixelFormat.MJPEG
            elif self.pixel_format == 'YUYV':
                fmt = PixelFormat.YUYV
            else:
                logger.warning(f"Unknown format {self.pixel_format}, defaulting to MJPEG")
                fmt = PixelFormat.MJPEG
            
            # Set format
            self.device.set_format(
                BufferType.VIDEO_CAPTURE,
                self.width,
                self.height,
                fmt
            )
            
            # Set frame rate (if supported)
            try:
                self.device.set_fps(BufferType.VIDEO_CAPTURE, self.fps)
            except Exception as e:
                logger.warning(f"Could not set FPS: {e}")
            
            # Get actual format (device may have adjusted)
            actual_fmt = self.device.get_format(BufferType.VIDEO_CAPTURE)
            logger.info(
                f"V4L2 capture opened: {self.device_path} @ "
                f"{actual_fmt.width}x{actual_fmt.height} "
                f"{actual_fmt.pixel_format.name} "
                f"(requested {self.width}x{self.height} {self.pixel_format})"
            )
            
            # Update with actual values
            self.width = actual_fmt.width
            self.height = actual_fmt.height
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to open V4L2 device: {e}")
            return False
    
    def close(self):
        """Close V4L2 device and release resources."""
        self.stop()
        if self.device:
            try:
                self.device.close()
            except Exception as e:
                logger.error(f"Error closing V4L2 device: {e}")
            finally:
                self.device = None
    
    def set_frame_callback(self, callback: Callable[[bytes, float], None]):
        """
        Set callback function to receive captured frames.
        
        Args:
            callback: Function called with (frame_data, timestamp_seconds)
        """
        self.frame_callback = callback
    
    def start(self) -> bool:
        """
        Start video capture in background thread.
        
        Returns:
            True if started successfully
        """
        with self.lock:
            if self.running:
                logger.warning("V4L2 capture already running")
                return False
            
            if not self.device:
                logger.error("V4L2 device not opened")
                return False
            
            try:
                # Request mmap buffers
                self.device.request_buffers(BufferType.VIDEO_CAPTURE, self.buffer_count)
                
                # Queue all buffers
                for i in range(self.buffer_count):
                    self.device.queue_buffer(BufferType.VIDEO_CAPTURE, i)
                
                # Start streaming
                self.device.stream_on(BufferType.VIDEO_CAPTURE)
                
                self.running = True
                self.frame_count = 0
                self.dropped_frames = 0
                
                # Start capture thread
                self.capture_thread = threading.Thread(
                    target=self._capture_loop,
                    daemon=True,
                    name="V4L2-Capture"
                )
                self.capture_thread.start()
                
                logger.info(f"V4L2 capture started with {self.buffer_count} buffers")
                return True
                
            except Exception as e:
                logger.error(f"Failed to start V4L2 capture: {e}")
                self.running = False
                return False
    
    def stop(self):
        """Stop video capture."""
        with self.lock:
            if not self.running:
                return
            
            self.running = False
        
        # Wait for capture thread to exit
        if self.capture_thread and self.capture_thread.is_alive():
            self.capture_thread.join(timeout=2.0)
        
        if self.device:
            try:
                self.device.stream_off(BufferType.VIDEO_CAPTURE)
            except Exception as e:
                logger.error(f"Error stopping stream: {e}")
        
        logger.info("V4L2 capture stopped")
    
    def _capture_loop(self):
        """
        Main capture loop - runs in background thread.
        
        Continuously dequeues buffers, processes frames, and requeues buffers.
        Uses zero-copy mmap access for minimal latency.
        """
        logger.debug("V4L2 capture loop started")
        
        while self.running:
            try:
                # Dequeue buffer (blocks until frame available)
                # Timeout prevents hanging if device disconnects
                buffer = self.device.dequeue_buffer(
                    BufferType.VIDEO_CAPTURE,
                    timeout=1.0
                )
                
                if not buffer:
                    continue
                
                capture_start = time.monotonic()
                
                # Get frame data from mmap'd buffer (zero-copy)
                frame_data = bytes(buffer.data)
                
                # Get timestamp from V4L2 buffer
                # buffer.timestamp is in microseconds
                timestamp = buffer.timestamp / 1_000_000.0  # Convert to seconds
                
                # Update statistics
                self.frame_count += 1
                capture_time_ms = (time.monotonic() - capture_start) * 1000
                
                # Running average of capture time
                alpha = 0.1  # Exponential moving average factor
                self.avg_capture_time_ms = (
                    alpha * capture_time_ms +
                    (1 - alpha) * self.avg_capture_time_ms
                )
                
                # Detect dropped frames (if timestamp jumps)
                if self.last_timestamp > 0:
                    expected_delta = 1.0 / self.fps
                    actual_delta = timestamp - self.last_timestamp
                    if actual_delta > expected_delta * 1.5:
                        drops = int(actual_delta / expected_delta) - 1
                        self.dropped_frames += drops
                        logger.debug(f"Detected {drops} dropped frames")
                
                self.last_timestamp = timestamp
                
                # Call frame callback if set
                if self.frame_callback:
                    try:
                        self.frame_callback(frame_data, timestamp)
                    except Exception as e:
                        logger.error(f"Error in frame callback: {e}")
                
                # Requeue buffer for next frame
                self.device.queue_buffer(BufferType.VIDEO_CAPTURE, buffer.index)
                
            except TimeoutError:
                # Dequeue timed out - this is normal if no frames available
                continue
            except Exception as e:
                if self.running:
                    logger.error(f"Error in capture loop: {e}")
                    # Brief sleep to avoid tight error loop
                    time.sleep(0.1)
        
        logger.debug("V4L2 capture loop exited")
    
    def get_stats(self) -> dict:
        """
        Get capture statistics.
        
        Returns:
            Dictionary with frame count, dropped frames, and timing info
        """
        return {
            'frame_count': self.frame_count,
            'dropped_frames': self.dropped_frames,
            'avg_capture_time_ms': round(self.avg_capture_time_ms, 2),
            'device': self.device_path,
            'resolution': f"{self.width}x{self.height}",
            'fps': self.fps,
            'format': self.pixel_format,
            'buffer_count': self.buffer_count
        }
