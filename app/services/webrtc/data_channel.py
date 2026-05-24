"""
WebRTC Data Channel for HID Input.

Provides low-latency keyboard and mouse input via WebRTC data channel
using a compact binary protocol.
"""

import logging
import struct
from typing import Optional
from enum import IntEnum

logger = logging.getLogger(__name__)


class HIDMessageType(IntEnum):
    """HID message types for data channel protocol."""
    KEYBOARD_KEY = 0x01
    KEYBOARD_MODIFIER = 0x02
    KEYBOARD_TEXT = 0x03
    MOUSE_MOVE = 0x04
    MOUSE_BUTTON = 0x05
    MOUSE_WHEEL = 0x06
    MOUSE_ABSOLUTE = 0x07
    SPECIAL_KEY = 0x08


class HIDDataChannel:
    """
    WebRTC data channel handler for HID input.
    
    Implements a binary protocol for efficient transmission of keyboard
    and mouse events over WebRTC data channel.
    
    Protocol format:
    - Keyboard key: [0x01][keycode:1][pressed:1]
    - Keyboard modifier: [0x02][keycode:1][modifiers:1]
    - Keyboard text: [0x03][length:2][text:N]
    - Mouse move: [0x04][dx:2][dy:2]
    - Mouse button: [0x05][button:1][pressed:1]
    - Mouse wheel: [0x06][delta:2]
    - Mouse absolute: [0x07][x:2][y:2]
    - Special key: [0x08][key_id:1]
    
    All multi-byte values are little-endian signed integers.
    """
    
    def __init__(self, data_channel, hid_controller):
        """
        Initialize HID data channel.
        
        Args:
            data_channel: RTCDataChannel instance
            hid_controller: HID controller for hardware communication
        """
        self.data_channel = data_channel
        self.hid_controller = hid_controller
        
        # Statistics
        self.messages_received = 0
        self.messages_processed = 0
        self.messages_failed = 0
        self.batched_events = 0
        
        # Event batching
        self.batch_buffer = []
        self.batch_timeout = 0.005  # 5ms batch window
        
        # Set up data channel handlers
        @data_channel.on("open")
        def on_open():
            logger.info("HID data channel opened")
        
        @data_channel.on("close")
        def on_close():
            logger.info("HID data channel closed")
        
        @data_channel.on("message")
        def on_message(message):
            self._handle_message(message)
    
    def _handle_message(self, message):
        """
        Handle incoming data channel message.
        
        Args:
            message: Binary message data
        """
        self.messages_received += 1
        
        if isinstance(message, str):
            # JSON fallback for compatibility
            self._handle_json_message(message)
            return
        
        try:
            # Binary protocol
            if len(message) < 1:
                logger.warning("Empty HID message received")
                return
            
            msg_type = message[0]
            
            if msg_type == HIDMessageType.KEYBOARD_KEY:
                self._handle_keyboard_key(message)
            elif msg_type == HIDMessageType.KEYBOARD_MODIFIER:
                self._handle_keyboard_modifier(message)
            elif msg_type == HIDMessageType.KEYBOARD_TEXT:
                self._handle_keyboard_text(message)
            elif msg_type == HIDMessageType.MOUSE_MOVE:
                self._handle_mouse_move(message)
            elif msg_type == HIDMessageType.MOUSE_BUTTON:
                self._handle_mouse_button(message)
            elif msg_type == HIDMessageType.MOUSE_WHEEL:
                self._handle_mouse_wheel(message)
            elif msg_type == HIDMessageType.MOUSE_ABSOLUTE:
                self._handle_mouse_absolute(message)
            elif msg_type == HIDMessageType.SPECIAL_KEY:
                self._handle_special_key(message)
            else:
                logger.warning(f"Unknown HID message type: {msg_type}")
                self.messages_failed += 1
                return
            
            self.messages_processed += 1
        
        except Exception as e:
            logger.error(f"Error handling HID message: {e}")
            self.messages_failed += 1
    
    def _handle_keyboard_key(self, message: bytes):
        """Handle keyboard key press/release."""
        if len(message) < 3:
            logger.warning("Invalid keyboard key message")
            return
        
        keycode = message[1]
        pressed = bool(message[2])
        
        if self.hid_controller and self.hid_controller.connected:
            self.hid_controller.send_key(keycode, pressed)
    
    def _handle_keyboard_modifier(self, message: bytes):
        """Handle keyboard key with modifiers."""
        if len(message) < 3:
            logger.warning("Invalid keyboard modifier message")
            return
        
        keycode = message[1]
        modifiers = message[2]
        
        if self.hid_controller and self.hid_controller.connected:
            self.hid_controller.send_key_with_modifier(keycode, modifiers)
    
    def _handle_keyboard_text(self, message: bytes):
        """Handle keyboard text input."""
        if len(message) < 3:
            logger.warning("Invalid keyboard text message")
            return
        
        # Length is 2-byte little-endian
        length = struct.unpack('<H', message[1:3])[0]
        
        if len(message) < 3 + length:
            logger.warning("Incomplete keyboard text message")
            return
        
        text = message[3:3+length].decode('utf-8', errors='replace')
        
        if self.hid_controller and self.hid_controller.connected:
            self.hid_controller.send_text(text)
    
    def _handle_mouse_move(self, message: bytes):
        """Handle relative mouse movement."""
        if len(message) < 5:
            logger.warning("Invalid mouse move message")
            return
        
        # dx and dy are 2-byte little-endian signed integers
        dx, dy = struct.unpack('<hh', message[1:5])
        
        if self.hid_controller and self.hid_controller.connected:
            self.hid_controller.move_mouse(dx, dy)
    
    def _handle_mouse_button(self, message: bytes):
        """Handle mouse button press/release."""
        if len(message) < 3:
            logger.warning("Invalid mouse button message")
            return
        
        button = message[1]  # 1=left, 2=right, 3=middle
        pressed = bool(message[2])
        
        if self.hid_controller and self.hid_controller.connected:
            button_map = {1: 'left', 2: 'right', 3: 'middle'}
            if button in button_map:
                self.hid_controller.click_mouse(button_map[button], pressed)
    
    def _handle_mouse_wheel(self, message: bytes):
        """Handle mouse wheel scroll."""
        if len(message) < 3:
            logger.warning("Invalid mouse wheel message")
            return
        
        # Delta is 2-byte little-endian signed integer
        delta = struct.unpack('<h', message[1:3])[0]
        
        if self.hid_controller and self.hid_controller.connected:
            self.hid_controller.scroll_wheel(delta)
    
    def _handle_mouse_absolute(self, message: bytes):
        """Handle absolute mouse positioning."""
        if len(message) < 5:
            logger.warning("Invalid mouse absolute message")
            return
        
        # x and y are 2-byte little-endian unsigned integers
        x, y = struct.unpack('<HH', message[1:5])
        
        if self.hid_controller and self.hid_controller.connected:
            self.hid_controller.move_mouse_absolute(x, y)
    
    def _handle_special_key(self, message: bytes):
        """Handle special key combinations."""
        if len(message) < 2:
            logger.warning("Invalid special key message")
            return
        
        key_id = message[1]
        
        # Special key map
        special_keys = {
            0x01: 'ctrl_alt_del',
            0x02: 'enter',
            0x03: 'escape',
            0x04: 'tab',
        }
        
        if key_id in special_keys and self.hid_controller and self.hid_controller.connected:
            key_name = special_keys[key_id]
            if key_name == 'ctrl_alt_del':
                self.hid_controller.send_ctrl_alt_del()
            # Add other special key handlers as needed
    
    def _handle_json_message(self, message: str):
        """
        Handle JSON message (fallback/compatibility mode).
        
        Args:
            message: JSON string
        """
        import json
        
        try:
            data = json.loads(message)
            action = data.get('action')
            
            if not self.hid_controller or not self.hid_controller.connected:
                return
            
            if action == 'key':
                keycode = data.get('keycode')
                pressed = data.get('pressed', True)
                self.hid_controller.send_key(keycode, pressed)
            
            elif action == 'key_with_modifier':
                keycode = data.get('keycode')
                modifiers = data.get('modifiers', 0)
                self.hid_controller.send_key_with_modifier(keycode, modifiers)
            
            elif action == 'text':
                text = data.get('text', '')
                self.hid_controller.send_text(text)
            
            elif action == 'mouse_move':
                dx = data.get('dx', 0)
                dy = data.get('dy', 0)
                self.hid_controller.move_mouse(dx, dy)
            
            elif action == 'mouse_click':
                button = data.get('button', 'left')
                pressed = data.get('pressed', True)
                self.hid_controller.click_mouse(button, pressed)
            
            elif action == 'mouse_wheel':
                delta = data.get('delta', 0)
                self.hid_controller.scroll_wheel(delta)
            
            elif action == 'mouse_absolute':
                x = data.get('x', 0)
                y = data.get('y', 0)
                self.hid_controller.move_mouse_absolute(x, y)
            
            self.messages_processed += 1
        
        except Exception as e:
            logger.error(f"Error handling JSON HID message: {e}")
            self.messages_failed += 1
    
    def send_status(self, status: dict):
        """
        Send status update to client.
        
        Args:
            status: Status dictionary to send
        """
        import json
        
        if self.data_channel.readyState == 'open':
            try:
                message = json.dumps(status)
                self.data_channel.send(message)
            except Exception as e:
                logger.error(f"Error sending status: {e}")
    
    def get_stats(self) -> dict:
        """
        Get data channel statistics.
        
        Returns:
            Statistics dictionary
        """
        return {
            'messages_received': self.messages_received,
            'messages_processed': self.messages_processed,
            'messages_failed': self.messages_failed,
            'success_rate': (
                self.messages_processed / self.messages_received * 100
                if self.messages_received > 0 else 0
            ),
            'ready_state': self.data_channel.readyState if hasattr(self.data_channel, 'readyState') else 'unknown',
        }
