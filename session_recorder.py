import os
import json
import time
import threading
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


class SessionRecorder:
    """
    Records KVM session video frames and input events for audit,
    compliance, or troubleshooting.

    Recordings are stored as directories containing:
        - frames/     JPEG frames with sequential numbering
        - events.jsonl  Input events (keyboard, mouse) as JSON lines
        - meta.json   Recording metadata (start time, user, duration, etc.)

    Playback is handled client-side using the stored frames and events.
    """

    def __init__(self, recordings_dir='/var/lib/kvm/recordings', max_recordings=50):
        """
        Initialize session recorder.

        Args:
            recordings_dir: Base directory for storing recordings.
            max_recordings: Maximum number of recordings to keep.
        """
        self.recordings_dir = Path(recordings_dir)
        self.max_recordings = max_recordings

        self._recording = False
        self._current_id = None
        self._current_dir = None
        self._frame_count = 0
        self._event_file = None
        self._start_time = None
        self._username = None
        self._lock = threading.Lock()
        self._frame_interval = 1.0  # seconds between saved frames
        self._last_frame_time = 0

    def setup(self):
        """Create recordings directory if needed."""
        self.recordings_dir.mkdir(parents=True, exist_ok=True)

    @property
    def is_recording(self):
        return self._recording

    def start_recording(self, username='unknown'):
        """
        Start a new recording session.

        Args:
            username: User who initiated the recording.

        Returns:
            Recording ID string, or None on error.
        """
        with self._lock:
            if self._recording:
                return self._current_id

            self.setup()
            self._cleanup_old_recordings()

            ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
            self._current_id = f'{ts}_{username}'
            self._current_dir = self.recordings_dir / self._current_id
            frames_dir = self._current_dir / 'frames'
            frames_dir.mkdir(parents=True, exist_ok=True)

            self._frame_count = 0
            self._start_time = time.time()
            self._username = username
            self._last_frame_time = 0

            # Open events file
            events_path = self._current_dir / 'events.jsonl'
            self._event_file = open(events_path, 'w')

            # Write metadata
            meta = {
                'id': self._current_id,
                'username': username,
                'start_time': datetime.utcnow().isoformat(),
                'frame_interval': self._frame_interval,
            }
            meta_path = self._current_dir / 'meta.json'
            meta_path.write_text(json.dumps(meta, indent=2))

            self._recording = True
            logger.info(f"Recording started: {self._current_id}")
            return self._current_id

    def stop_recording(self):
        """
        Stop the current recording session.

        Returns:
            Recording ID of the stopped session, or None.
        """
        with self._lock:
            if not self._recording:
                return None

            rec_id = self._current_id
            duration = time.time() - self._start_time

            # Close events file
            if self._event_file:
                self._event_file.close()
                self._event_file = None

            # Update metadata with end info
            meta_path = self._current_dir / 'meta.json'
            try:
                meta = json.loads(meta_path.read_text())
                meta['end_time'] = datetime.utcnow().isoformat()
                meta['duration_seconds'] = round(duration, 1)
                meta['frame_count'] = self._frame_count
                meta_path.write_text(json.dumps(meta, indent=2))
            except Exception as e:
                logger.error(f"Failed to update recording metadata: {e}")

            self._recording = False
            self._current_id = None
            self._current_dir = None

            logger.info(f"Recording stopped: {rec_id} ({self._frame_count} frames, {duration:.1f}s)")
            return rec_id

    def record_frame(self, jpeg_data):
        """
        Save a video frame to the current recording.

        Called from the video stream loop. Respects frame_interval to
        avoid saving every single frame (which would use too much disk).

        Args:
            jpeg_data: Raw JPEG bytes of the frame.
        """
        if not self._recording or not jpeg_data:
            return

        now = time.time()
        if (now - self._last_frame_time) < self._frame_interval:
            return

        with self._lock:
            if not self._recording:
                return
            try:
                frame_path = self._current_dir / 'frames' / f'{self._frame_count:06d}.jpg'
                frame_path.write_bytes(jpeg_data)
                self._frame_count += 1
                self._last_frame_time = now
            except Exception as e:
                logger.error(f"Failed to save recording frame: {e}")

    def record_event(self, event_data):
        """
        Record an input event (keyboard, mouse, etc.).

        Args:
            event_data: Dict with event info (type, keycode, x, y, etc.)
        """
        if not self._recording:
            return

        with self._lock:
            if not self._recording or not self._event_file:
                return
            try:
                entry = {
                    't': round(time.time() - self._start_time, 3),
                    **event_data,
                }
                self._event_file.write(json.dumps(entry) + '\n')
                self._event_file.flush()
            except Exception as e:
                logger.error(f"Failed to save recording event: {e}")

    def list_recordings(self):
        """
        List all available recordings.

        Returns:
            List of recording metadata dicts, newest first.
        """
        recordings = []
        if not self.recordings_dir.exists():
            return recordings

        for entry in sorted(self.recordings_dir.iterdir(), reverse=True):
            if not entry.is_dir():
                continue
            meta_path = entry / 'meta.json'
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text())
                    # Calculate size
                    total_size = sum(
                        f.stat().st_size for f in entry.rglob('*') if f.is_file()
                    )
                    meta['size_bytes'] = total_size
                    meta['size_human'] = self._human_size(total_size)
                    recordings.append(meta)
                except Exception:
                    recordings.append({'id': entry.name, 'error': 'corrupt metadata'})
            else:
                recordings.append({'id': entry.name, 'error': 'no metadata'})

        return recordings

    def get_recording(self, recording_id):
        """
        Get metadata and frame list for a recording.

        Returns:
            Dict with metadata and frame count, or None.
        """
        safe_id = self._sanitize_id(recording_id)
        rec_dir = self.recordings_dir / safe_id
        if not rec_dir.exists():
            return None

        meta_path = rec_dir / 'meta.json'
        if not meta_path.exists():
            return None

        try:
            meta = json.loads(meta_path.read_text())
            frames_dir = rec_dir / 'frames'
            frame_files = sorted(frames_dir.glob('*.jpg')) if frames_dir.exists() else []
            meta['frame_count'] = len(frame_files)
            return meta
        except Exception:
            return None

    def get_frame(self, recording_id, frame_number):
        """
        Get a specific frame from a recording.

        Returns:
            JPEG bytes, or None.
        """
        safe_id = self._sanitize_id(recording_id)
        frame_path = self.recordings_dir / safe_id / 'frames' / f'{frame_number:06d}.jpg'
        if frame_path.exists():
            return frame_path.read_bytes()
        return None

    def get_events(self, recording_id):
        """
        Get all events from a recording.

        Returns:
            List of event dicts, or empty list.
        """
        safe_id = self._sanitize_id(recording_id)
        events_path = self.recordings_dir / safe_id / 'events.jsonl'
        if not events_path.exists():
            return []

        events = []
        try:
            with open(events_path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        events.append(json.loads(line))
        except Exception:
            pass
        return events

    def delete_recording(self, recording_id):
        """
        Delete a recording.

        Returns:
            True if deleted, False otherwise.
        """
        import shutil
        safe_id = self._sanitize_id(recording_id)
        rec_dir = self.recordings_dir / safe_id
        if not rec_dir.exists():
            return False

        try:
            rec_dir.resolve().relative_to(self.recordings_dir.resolve())
        except ValueError:
            return False

        try:
            shutil.rmtree(rec_dir)
            logger.info(f"Recording deleted: {safe_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete recording {safe_id}: {e}")
            return False

    def set_frame_interval(self, interval):
        """Set seconds between captured frames (lower = more detail, more disk)."""
        self._frame_interval = max(0.25, min(10.0, float(interval)))

    def _cleanup_old_recordings(self):
        """Remove oldest recordings if over max limit."""
        if not self.recordings_dir.exists():
            return
        import shutil
        dirs = sorted(
            [d for d in self.recordings_dir.iterdir() if d.is_dir()],
            key=lambda d: d.name,
        )
        while len(dirs) > self.max_recordings:
            oldest = dirs.pop(0)
            try:
                shutil.rmtree(oldest)
                logger.info(f"Cleaned up old recording: {oldest.name}")
            except Exception:
                pass

    @staticmethod
    def _sanitize_id(recording_id):
        """Sanitize recording ID to prevent path traversal."""
        import re
        return re.sub(r'[^\w.\-]', '_', str(recording_id))

    @staticmethod
    def _human_size(nbytes):
        for unit in ('B', 'KB', 'MB', 'GB'):
            if abs(nbytes) < 1024.0:
                return f'{nbytes:.1f} {unit}'
            nbytes /= 1024.0
        return f'{nbytes:.1f} TB'
