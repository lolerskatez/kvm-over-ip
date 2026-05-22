import serial
import struct
import time
import logging

logger = logging.getLogger(__name__)

class CH9329HIDController:
    """
    Controller for CH9329 USB HID device over serial interface.
    Encodes and sends HID packets for keyboard and mouse control.
    """
    
    HEADER = 0x55
    FOOTER = 0xAA
    
    HID_TYPE_KEYBOARD = 0x01
    HID_TYPE_MOUSE = 0x02
    HID_TYPE_MOUSE_ABS = 0x04
    
    MOUSE_ABS_MAX = 32767
    
    def __init__(self, port='/dev/ttyUSB0', baudrate=9600, timeout=1):
        """
        Initialize HID controller.
        
        Args:
            port: Serial port path (e.g., /dev/ttyUSB0)
            baudrate: Serial communication speed
            timeout: Serial read timeout in seconds
        """
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.serial = None
        self.connected = False
        self.mouse_mode = 'absolute'  # 'absolute' or 'relative'
        self.mouse_buttons = 0x00
    
    def connect(self):
        """Open serial connection to CH9329 device."""
        try:
            self.serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.timeout
            )
            self.connected = True
            logger.info(f"Connected to HID device on {self.port}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to HID device: {e}")
            self.connected = False
            return False
    
    def disconnect(self):
        """Close serial connection."""
        if self.serial and self.serial.is_open:
            self.serial.close()
            self.connected = False
            logger.info("Disconnected from HID device")
    
    def _calculate_checksum(self, data):
        """Calculate checksum for packet (sum of all bytes mod 256)."""
        return sum(data) & 0xFF
    
    def _send_packet(self, hid_type, data):
        """
        Send HID packet to device.
        
        Packet format:
        [HEADER][LENGTH][TYPE][DATA...][CHECKSUM][FOOTER]
        """
        if not self.connected:
            logger.warning("HID device not connected")
            return False
        
        try:
            length = len(data) + 1
            packet = bytes([self.HEADER, length, hid_type]) + data
            checksum = self._calculate_checksum(packet[1:])
            packet += bytes([checksum, self.FOOTER])
            
            self.serial.write(packet)
            logger.debug(f"Sent HID packet: {packet.hex()}")
            return True
        except Exception as e:
            logger.error(f"Failed to send HID packet: {e}")
            return False
    
    def send_key(self, keycode, pressed=True):
        """
        Send keyboard key event.
        
        Args:
            keycode: USB HID keycode (1-101)
            pressed: True for key press, False for key release
        """
        keycode = int(keycode)
        modifier = 0x00
        reserved = 0x00
        key_array = bytes([keycode, 0, 0, 0, 0, 0])
        
        data = bytes([modifier, reserved]) + key_array
        return self._send_packet(self.HID_TYPE_KEYBOARD, data)
    
    def send_key_with_modifier(self, keycode, modifiers):
        """
        Send keyboard key with modifier keys.
        
        Args:
            keycode: USB HID keycode
            modifiers: Modifier byte (bit flags):
                0x01 = Left Ctrl
                0x02 = Left Shift
                0x04 = Left Alt
                0x08 = Left GUI
                0x10 = Right Ctrl
                0x20 = Right Shift
                0x40 = Right Alt
                0x80 = Right GUI
        """
        keycode = int(keycode)
        modifiers = int(modifiers)
        reserved = 0x00
        key_array = bytes([keycode, 0, 0, 0, 0, 0])
        
        data = bytes([modifiers, reserved]) + key_array
        return self._send_packet(self.HID_TYPE_KEYBOARD, data)
    
    def send_mouse_move(self, x, y, wheel=0):
        """
        Send mouse movement (relative coordinates).
        
        Args:
            x: Relative X movement (-127 to 127)
            y: Relative Y movement (-127 to 127)
            wheel: Wheel movement (-127 to 127)
        """
        x = max(-127, min(127, int(x))) & 0xFF
        y = max(-127, min(127, int(y))) & 0xFF
        wheel = max(-127, min(127, int(wheel))) & 0xFF
        
        data = bytes([self.mouse_buttons, x, y, wheel])
        return self._send_packet(self.HID_TYPE_MOUSE, data)
    
    def send_mouse_move_absolute(self, x, y, wheel=0):
        """
        Send absolute mouse position (tablet mode).
        
        Args:
            x: Absolute X position (0-32767)
            y: Absolute Y position (0-32767)
            wheel: Wheel movement (-127 to 127)
        """
        x = max(0, min(self.MOUSE_ABS_MAX, int(x)))
        y = max(0, min(self.MOUSE_ABS_MAX, int(y)))
        wheel = max(-127, min(127, int(wheel))) & 0xFF
        
        x_low = x & 0xFF
        x_high = (x >> 8) & 0xFF
        y_low = y & 0xFF
        y_high = (y >> 8) & 0xFF
        
        data = bytes([self.mouse_buttons, x_low, x_high, y_low, y_high, wheel])
        return self._send_packet(self.HID_TYPE_MOUSE_ABS, data)
    
    def send_mouse_click(self, button='left', pressed=True):
        """
        Send mouse button click.
        
        Args:
            button: 'left', 'right', or 'middle'
            pressed: True for press, False for release
        """
        button_map = {
            'left': 0x01,
            'right': 0x02,
            'middle': 0x04
        }
        
        bit = button_map.get(button, 0x00)
        if pressed:
            self.mouse_buttons |= bit
        else:
            self.mouse_buttons &= ~bit
        
        if self.mouse_mode == 'absolute':
            data = bytes([self.mouse_buttons, 0, 0, 0, 0, 0])
            return self._send_packet(self.HID_TYPE_MOUSE_ABS, data)
        else:
            data = bytes([self.mouse_buttons, 0, 0, 0])
            return self._send_packet(self.HID_TYPE_MOUSE, data)
    
    def set_mouse_mode(self, mode):
        """
        Set mouse input mode.
        
        Args:
            mode: 'absolute' or 'relative'
        """
        if mode in ('absolute', 'relative'):
            self.mouse_mode = mode
            self.mouse_buttons = 0x00
            logger.info(f"Mouse mode set to {mode}")
            return True
        return False
    
    def send_ctrl_alt_del(self):
        """Send Ctrl+Alt+Del sequence."""
        modifier = 0x01 | 0x04
        keycode = 0x4C
        reserved = 0x00
        key_array = bytes([keycode, 0, 0, 0, 0, 0])
        
        data = bytes([modifier, reserved]) + key_array
        return self._send_packet(self.HID_TYPE_KEYBOARD, data)
    
    def send_text(self, text, delay=0.05):
        """
        Send text as keyboard input.
        
        Args:
            text: String to send
            delay: Delay between keystrokes in seconds
        """
        keycode_map = {
            'a': 0x04, 'b': 0x05, 'c': 0x06, 'd': 0x07, 'e': 0x08,
            'f': 0x09, 'g': 0x0A, 'h': 0x0B, 'i': 0x0C, 'j': 0x0D,
            'k': 0x0E, 'l': 0x0F, 'm': 0x10, 'n': 0x11, 'o': 0x12,
            'p': 0x13, 'q': 0x14, 'r': 0x15, 's': 0x16, 't': 0x17,
            'u': 0x18, 'v': 0x19, 'w': 0x1A, 'x': 0x1B, 'y': 0x1C,
            'z': 0x1D,
            '0': 0x27, '1': 0x1E, '2': 0x1F, '3': 0x20, '4': 0x21,
            '5': 0x22, '6': 0x23, '7': 0x24, '8': 0x25, '9': 0x26,
            ' ': 0x2C, '-': 0x2D, '=': 0x2E, '[': 0x2F, ']': 0x30,
            ';': 0x33, '\'': 0x34, '`': 0x35, ',': 0x36, '.': 0x37,
            '/': 0x38,
        }
        
        for char in text:
            keycode = keycode_map.get(char.lower())
            if keycode:
                modifier = 0x02 if char.isupper() else 0x00
                self.send_key_with_modifier(keycode, modifier)
                time.sleep(delay)
