#!/usr/bin/env python3
"""
Test script for HID controller functionality.
Run this to verify CH9329 device communication.
"""

import sys
import time
from hid_controller import CH9329HIDController


def test_connection(port='/dev/ttyUSB0'):
    """Test HID device connection."""
    print(f"Testing connection to {port}...")
    hid = CH9329HIDController(port=port)
    
    if hid.connect():
        print("✓ Connected successfully")
        hid.disconnect()
        return True
    else:
        print("✗ Connection failed")
        return False


def test_keyboard(port='/dev/ttyUSB0'):
    """Test keyboard functionality."""
    print("\nTesting keyboard input...")
    hid = CH9329HIDController(port=port)
    
    if not hid.connect():
        print("✗ Failed to connect")
        return False
    
    try:
        print("Sending 'A' key...")
        hid.send_key(0x04)
        time.sleep(0.1)
        
        print("Sending 'Enter' key...")
        hid.send_key(0x28)
        time.sleep(0.1)
        
        print("✓ Keyboard test passed")
        return True
    except Exception as e:
        print(f"✗ Keyboard test failed: {e}")
        return False
    finally:
        hid.disconnect()


def test_mouse(port='/dev/ttyUSB0'):
    """Test mouse functionality."""
    print("\nTesting mouse input...")
    hid = CH9329HIDController(port=port)
    
    if not hid.connect():
        print("✗ Failed to connect")
        return False
    
    try:
        print("Moving mouse right 10 pixels...")
        hid.send_mouse_move(10, 0)
        time.sleep(0.1)
        
        print("Moving mouse down 10 pixels...")
        hid.send_mouse_move(0, 10)
        time.sleep(0.1)
        
        print("Clicking left button...")
        hid.send_mouse_click('left', True)
        time.sleep(0.1)
        hid.send_mouse_click('left', False)
        time.sleep(0.1)
        
        print("✓ Mouse test passed")
        return True
    except Exception as e:
        print(f"✗ Mouse test failed: {e}")
        return False
    finally:
        hid.disconnect()


def test_text(port='/dev/ttyUSB0'):
    """Test text input."""
    print("\nTesting text input...")
    hid = CH9329HIDController(port=port)
    
    if not hid.connect():
        print("✗ Failed to connect")
        return False
    
    try:
        print("Sending text 'hello'...")
        hid.send_text('hello', delay=0.05)
        time.sleep(0.1)
        
        print("✓ Text input test passed")
        return True
    except Exception as e:
        print(f"✗ Text input test failed: {e}")
        return False
    finally:
        hid.disconnect()


def main():
    """Run all tests."""
    print("=== KVM-over-IP HID Controller Test ===\n")
    
    port = '/dev/ttyUSB0'
    if len(sys.argv) > 1:
        port = sys.argv[1]
    
    tests = [
        ("Connection", lambda: test_connection(port)),
        ("Keyboard", lambda: test_keyboard(port)),
        ("Mouse", lambda: test_mouse(port)),
        ("Text Input", lambda: test_text(port)),
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
