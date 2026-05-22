#!/usr/bin/env python3
"""
Test script for video streaming functionality.
Run this to verify video capture device and FFmpeg setup.
"""

import sys
import subprocess
from pathlib import Path


def test_device_exists(device='/dev/video0'):
    """Test if video device exists."""
    print(f"Checking if {device} exists...")
    
    if Path(device).exists():
        print(f"✓ Device {device} found")
        return True
    else:
        print(f"✗ Device {device} not found")
        return False


def test_ffmpeg_installed():
    """Test if FFmpeg is installed."""
    print("\nChecking FFmpeg installation...")
    
    try:
        result = subprocess.run(['ffmpeg', '-version'], 
                              capture_output=True, 
                              timeout=5)
        if result.returncode == 0:
            version = result.stdout.decode().split('\n')[0]
            print(f"✓ FFmpeg installed: {version}")
            return True
    except Exception as e:
        print(f"✗ FFmpeg not found: {e}")
        return False


def test_video_capture(device='/dev/video0', output='/tmp/test.jpg'):
    """Test video capture with FFmpeg."""
    print(f"\nTesting video capture from {device}...")
    
    if not Path(device).exists():
        print(f"✗ Device {device} not found")
        return False
    
    try:
        cmd = [
            'ffmpeg',
            '-f', 'v4l2',
            '-input_format', 'mjpeg',
            '-i', device,
            '-vf', 'scale=1280:720',
            '-frames:v', '1',
            '-y',
            output
        ]
        
        result = subprocess.run(cmd, 
                              capture_output=True, 
                              timeout=10)
        
        if result.returncode == 0 and Path(output).exists():
            size = Path(output).stat().st_size
            print(f"✓ Captured frame: {size} bytes")
            print(f"  Saved to: {output}")
            return True
        else:
            print(f"✗ Capture failed")
            if result.stderr:
                print(f"  Error: {result.stderr.decode()[:200]}")
            return False
    
    except subprocess.TimeoutExpired:
        print("✗ Capture timeout (device may not be responding)")
        return False
    except Exception as e:
        print(f"✗ Capture error: {e}")
        return False


def test_streaming(device='/dev/video0', duration=5):
    """Test MJPEG streaming."""
    print(f"\nTesting MJPEG stream from {device} for {duration}s...")
    
    if not Path(device).exists():
        print(f"✗ Device {device} not found")
        return False
    
    try:
        cmd = [
            'ffmpeg',
            '-f', 'v4l2',
            '-input_format', 'mjpeg',
            '-i', device,
            '-vf', 'scale=1280:720',
            '-r', '15',
            '-b:v', '2000k',
            '-f', 'mjpeg',
            'pipe:1'
        ]
        
        process = subprocess.Popen(cmd,
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE)
        
        bytes_read = 0
        frames = 0
        
        try:
            for _ in range(duration):
                chunk = process.stdout.read(1024)
                if chunk:
                    bytes_read += len(chunk)
                    if b'\xff\xd8' in chunk:
                        frames += 1
        except KeyboardInterrupt:
            pass
        finally:
            process.terminate()
            process.wait(timeout=5)
        
        if bytes_read > 0:
            print(f"✓ Stream test passed")
            print(f"  Bytes read: {bytes_read}")
            print(f"  Frames detected: {frames}")
            return True
        else:
            print(f"✗ No data received")
            return False
    
    except subprocess.TimeoutExpired:
        print("✗ Stream timeout")
        return False
    except Exception as e:
        print(f"✗ Stream error: {e}")
        return False


def test_device_permissions(device='/dev/video0'):
    """Test device permissions."""
    print(f"\nChecking permissions for {device}...")
    
    try:
        path = Path(device)
        if not path.exists():
            print(f"✗ Device {device} not found")
            return False
        
        stat = path.stat()
        mode = oct(stat.st_mode)[-3:]
        uid = stat.st_uid
        gid = stat.st_gid
        
        print(f"✓ Device info:")
        print(f"  Permissions: {mode}")
        print(f"  Owner UID: {uid}")
        print(f"  Group GID: {gid}")
        
        if stat.st_mode & 0o004:
            print(f"  ✓ Readable by others")
            return True
        else:
            print(f"  ✗ Not readable by others (may need udev rules)")
            return False
    
    except Exception as e:
        print(f"✗ Permission check error: {e}")
        return False


def main():
    """Run all video tests."""
    print("=== KVM-over-IP Video Streamer Test ===\n")
    
    device = '/dev/video0'
    if len(sys.argv) > 1:
        device = sys.argv[1]
    
    tests = [
        ("Device Exists", lambda: test_device_exists(device)),
        ("FFmpeg Installed", test_ffmpeg_installed),
        ("Device Permissions", lambda: test_device_permissions(device)),
        ("Video Capture", lambda: test_video_capture(device)),
        ("MJPEG Stream", lambda: test_streaming(device, duration=3)),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n✗ {name} test error: {e}")
            results.append((name, False))
    
    print("\n=== Test Results ===")
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {name}")
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    print(f"\nTotal: {passed}/{total} passed")
    
    return 0 if passed == total else 1


if __name__ == '__main__':
    sys.exit(main())
