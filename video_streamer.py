import subprocess
import threading
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class VideoStreamer:
    """
    Video streaming handler using FFmpeg.
    Captures from UVC device and provides MJPEG or H.264 stream.
    Supports quality adjustment and adaptive streaming.
    """
    
    # Predefined quality profiles for adaptive streaming
    QUALITY_PROFILES = {
        'low': {'resolution': '640x480', 'framerate': 10, 'bitrate': '500k'},
        'medium': {'resolution': '1024x768', 'framerate': 15, 'bitrate': '1500k'},
        'high': {'resolution': '1280x720', 'framerate': 25, 'bitrate': '3000k'},
        'ultra': {'resolution': '1920x1080', 'framerate': 30, 'bitrate': '5000k'},
    }
    
    def __init__(self, video_device='/dev/video0', resolution='1280x720', 
                 framerate=15, bitrate='2000k', codec='mjpeg', quality=50):
        """
        Initialize video streamer.
        
        Args:
            video_device: Path to video device (e.g., /dev/video0)
            resolution: Output resolution (e.g., 1280x720)
            framerate: Output framerate in FPS
            bitrate: Output bitrate (e.g., 2000k)
            codec: Video codec ('mjpeg' or 'h264')
            quality: Quality percentage (1-100) for adaptive streaming
        """
        self.video_device = video_device
        self.resolution = resolution
        self.framerate = framerate
        self.bitrate = bitrate
        self.codec = codec  # 'mjpeg' or 'h264'
        self.quality = max(1, min(100, quality))  # Clamp 1-100
        self.process = None
        self.running = False
        self.lock = threading.Lock()
        self._latest_frame = None
        self._frame_lock = threading.Lock()
        self._frame_count = 0
        self._byte_count = 0
        self._fps = 0.0
        self._bandwidth = 0
        self._stats_lock = threading.Lock()
        self._stats_time = time.monotonic()
        self._stats_frames = 0
        self._stats_bytes = 0
        self._record_callback = None
        self._consumer_thread = None  # Background thread to consume FFmpeg output
    
    def start(self):
        """Start video streaming process."""
        with self.lock:
            if self.running:
                logger.warning("Video streamer already running")
                return True
            
            if not Path(self.video_device).exists():
                logger.error(f"Video device not found: {self.video_device}")
                return False
            
            try:
                width, height = self.resolution.split('x')
                
                # Build FFmpeg command based on codec
                if self.codec == 'h264':
                    ffmpeg_cmd = [
                        'ffmpeg',
                        '-f', 'v4l2',
                        '-input_format', 'mjpeg',
                        '-i', self.video_device,
                        '-vf', f'scale={width}:{height}',
                        '-r', str(self.framerate),
                        '-c:v', 'libx264',
                        '-preset', 'ultrafast',  # ultrafast, superfast, veryfast, faster, fast, medium
                        '-crf', str(51 - (self.quality // 2)),  # Quality: lower=better, 51=worst, 0=best
                        '-b:v', self.bitrate,
                        '-f', 'h264',
                        'pipe:1'
                    ]
                else:  # MJPEG (default)
                    ffmpeg_cmd = [
                        'ffmpeg',
                        '-f', 'v4l2',
                        '-input_format', 'mjpeg',
                        '-i', self.video_device,
                        '-vf', f'scale={width}:{height}',
                        '-r', str(self.framerate),
                        '-b:v', self.bitrate,
                        '-f', 'mjpeg',
                        'pipe:1'
                    ]
                
                self.process = subprocess.Popen(
                    ffmpeg_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=10*1024*1024
                )
                
                # Give FFmpeg a moment to initialize and check if it crashed immediately
                import time
                time.sleep(0.5)
                
                if self.process.poll() is not None:
                    # Process exited immediately - capture stderr output
                    stderr_output = self.process.stderr.read().decode('utf-8', errors='replace')
                    logger.error(f"FFmpeg exited immediately with code {self.process.returncode}")
                    if stderr_output:
                        logger.error(f"FFmpeg stderr: {stderr_output}")
                    self.process = None
                    self.running = False
                    return False
                
                # Start thread to log FFmpeg stderr output (only if process is still running)
                def log_ffmpeg_stderr():
                    for line in self.process.stderr:
                        line_str = line.decode('utf-8', errors='replace').strip()
                        if line_str:
                            logger.debug(f"FFmpeg: {line_str}")
                
                stderr_thread = threading.Thread(target=log_ffmpeg_stderr, daemon=True)
                stderr_thread.start()
                
                # Start background consumer thread to prevent pipe from filling up
                self._consumer_thread = threading.Thread(target=self._consume_frames, daemon=True)
                self._consumer_thread.start()
                
                self.running = True
                logger.info(f"Video streamer started: {self.video_device} @ {self.resolution} {self.framerate}fps ({self.codec.upper()})")
                return True
            
            except Exception as e:
                logger.error(f"Failed to start video streamer: {e}")
                self.running = False
                return False
    
    def stop(self):
        """Stop video streaming process."""
        with self.lock:
            self.running = False  # Signal threads to stop
            if self.process:
                try:
                    self.process.terminate()
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                except Exception as e:
                    logger.error(f"Error stopping streamer: {e}")
                finally:
                    self.process = None
                    logger.info("Video streamer stopped")
            
            # Wait for consumer thread to finish
            if self._consumer_thread and self._consumer_thread.is_alive():
                self._consumer_thread.join(timeout=2)
    
    def is_running(self):
        """Check if streamer is running."""
        with self.lock:
            if self.process:
                return self.process.poll() is None
            return False
    
    def _consume_frames(self):
        """
        Background thread that continuously reads frames from FFmpeg.
        This prevents the stdout pipe from filling up and blocking FFmpeg.
        """
        logger.debug("Frame consumer thread started")
        buffer = b''
        try:
            while self.running and self.process:
                # Read from FFmpeg stdout
                chunk = self.process.stdout.read(4096)
                if not chunk:
                    logger.debug("FFmpeg stdout closed")
                    break
                    
                buffer += chunk
                
                # Extract JPEG frames (MJPEG format)
                while True:
                    # Find start of image marker (SOI)
                    soi = buffer.find(b'\xff\xd8')
                    if soi == -1:
                        buffer = b''
                        break
                    
                    # Find end of image marker (EOI)
                    eoi = buffer.find(b'\xff\xd9', soi + 2)
                    if eoi == -1:
                        # Incomplete frame, keep buffer starting from SOI
                        buffer = buffer[soi:]
                        break
                    
                    # Extract complete frame
                    frame = buffer[soi:eoi + 2]
                    buffer = buffer[eoi + 2:]
                    
                    # Store latest frame
                    with self._frame_lock:
                        self._latest_frame = frame
                    
                    frame_len = len(frame)
                    
                    # Update statistics
                    with self._stats_lock:
                        self._frame_count += 1
                        self._byte_count += frame_len
                        self._stats_frames += 1
                        self._stats_bytes += frame_len
                        now = time.monotonic()
                        elapsed = now - self._stats_time
                        if elapsed >= 1.0:
                            self._fps = round(self._stats_frames / elapsed, 1)
                            self._bandwidth = round(self._stats_bytes / elapsed)
                            self._stats_frames = 0
                            self._stats_bytes = 0
                            self._stats_time = now
                    
                    # Call recording callback if set
                    cb = self._record_callback
                    if cb:
                        try:
                            cb(frame)
                        except Exception as e:
                            logger.error(f"Recording callback error: {e}")
                            
        except Exception as e:
            logger.error(f"Frame consumer thread error: {e}")
        finally:
            logger.debug("Frame consumer thread stopped")
    
    
    def get_stream_response(self):
        """
        Get MJPEG stream response for Flask.
        Returns a generator that yields properly framed MJPEG multipart responses.
        Reads from the latest frame captured by the background consumer thread.
        """
        if not self.is_running():
            return None
        
        def generate():
            """Generator for MJPEG stream with proper boundary framing."""
            last_frame_id = 0
            try:
                while self.is_running():
                    # Get latest frame
                    with self._frame_lock:
                        frame = self._latest_frame
                        current_frame_id = self._frame_count
                    
                    # Wait for new frame if we haven't seen this one yet
                    if frame and current_frame_id != last_frame_id:
                        last_frame_id = current_frame_id
                        
                        # Yield frame in multipart/x-mixed-replace format
                        yield (b'--jpegboundary\r\n'
                               b'Content-Type: image/jpeg\r\n'
                               b'Content-Length: ' + str(len(frame)).encode() + b'\r\n\r\n' +
                               frame + b'\r\n')
                    else:
                        # No new frame yet, wait a bit
                        time.sleep(0.033)  # ~30 FPS max
                        
            except Exception as e:
                logger.error(f"Stream generation error: {e}")
        
        from flask import Response
        return Response(
            generate(),
            mimetype='multipart/x-mixed-replace; boundary=--jpegboundary'
        )
    
    def capture_screenshot(self):
        """
        Capture the latest video frame as a JPEG screenshot.
        
        Returns:
            JPEG image bytes, or None if no frame is available.
        """
        with self._frame_lock:
            return self._latest_frame
    
    def update_settings(self, resolution=None, framerate=None, bitrate=None, 
                       codec=None, quality=None):
        """Update streamer settings (requires restart)."""
        changed = False
        
        if resolution and resolution != self.resolution:
            self.resolution = resolution
            changed = True
        
        if framerate and framerate != self.framerate:
            self.framerate = framerate
            changed = True
        
        if bitrate and bitrate != self.bitrate:
            self.bitrate = bitrate
            changed = True
        
        if codec and codec in ('mjpeg', 'h264') and codec != self.codec:
            self.codec = codec
            changed = True
        
        if quality is not None:
            new_quality = max(1, min(100, quality))
            if new_quality != self.quality:
                self.quality = new_quality
                changed = True
        
        if changed:
            self.stop()
            return self.start()
        
        return True
    
    def set_quality_adaptive(self, bandwidth_bps):
        """
        Automatically adjust quality based on available bandwidth.
        
        Args:
            bandwidth_bps: Current available bandwidth in bits per second
        """
        if bandwidth_bps < 1_000_000:  # < 1 Mbps
            profile = self.QUALITY_PROFILES['low']
        elif bandwidth_bps < 3_000_000:  # < 3 Mbps
            profile = self.QUALITY_PROFILES['medium']
        elif bandwidth_bps < 5_000_000:  # < 5 Mbps
            profile = self.QUALITY_PROFILES['high']
        else:
            profile = self.QUALITY_PROFILES['ultra']
        
        self.update_settings(
            resolution=profile['resolution'],
            framerate=profile['framerate'],
            bitrate=profile['bitrate']
        )

    def get_stream_stats(self):
        """Return current FPS, bandwidth (bytes/sec), and total frame count."""
        with self._stats_lock:
            return {
                'fps': self._fps,
                'bandwidth_bps': self._bandwidth,
                'total_frames': self._frame_count,
                'total_bytes': self._byte_count,
                'resolution': self.resolution,
                'target_fps': self.framerate,
                'bitrate': self.bitrate,
            }

    def set_record_callback(self, callback):
        """
        Set a callback that receives each JPEG frame for recording.

        Args:
            callback: Callable(jpeg_bytes) or None to disable.
        """
        self._record_callback = callback
