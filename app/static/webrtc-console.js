/**
 * WebRTC KVM Console - Low-Latency Remote Console Client
 * 
 * Implements WebRTC-based KVM-over-IP client with:
 * - RTCPeerConnection for video streaming
 * - RTCDataChannel for HID input (binary protocol)
 * - Latency measurement using requestVideoFrameCallback
 * - Automatic reconnection on failure
 * 
 * Binary Protocol (matches app/services/webrtc/data_channel.py):
 * - Keyboard: [0x01][keycode:1][pressed:1]
 * - Mouse Move: [0x02][dx:2][dy:2]
 * - Mouse Click: [0x03][button:1][pressed:1]
 * - Mouse Wheel: [0x04][delta_x:2][delta_y:2]
 * - Absolute Mouse: [0x05][x:2][y:2]
 */

const WebRTCConsole = (window.WebRTCConsole = class WebRTCConsole {
    constructor() {
        // WebRTC state
        this.peerConnection = null;
        this.dataChannel = null;
        this.sessionId = null;
        this.signalingState = 'disconnected';
        
        // Video state
        this.videoElement = null;
        this.streamActive = false;
        
        // Input state
        this.mouseX = 0;
        this.mouseY = 0;
        this.lastMouseX = 0;
        this.lastMouseY = 0;
        this.keysPressed = new Set();
        
        // Latency tracking
        this.latencyStats = {
            current: 0,
            min: Infinity,
            max: 0,
            avg: 0,
            p95: 0,
            samples: []
        };
        this.latencySampleSize = 100;
        
        // Configuration
        this.config = {
            iceServers: [
                { urls: 'stun:stun.l.google.com:19302' },
                { urls: 'stun:stun1.l.google.com:19302' }
            ],
            reconnectDelay: 3000,
            maxReconnectAttempts: 5
        };
        this.reconnectAttempts = 0;
        
        // Status update interval
        this.statusCheckInterval = null;
        
        this.init();
    }
    
    async init() {
        console.log('[WebRTC] Initializing WebRTC console...');
        
        this.videoElement = document.getElementById('video-stream');
        
        if (!this.videoElement) {
            console.error('[WebRTC] Video element not found');
            return;
        }
        
        this.setupEventListeners();
        await this.connect();
        this.startStatusCheck();
    }
    
    setupEventListeners() {
        // Video interactions
        const videoContainer = this.videoElement.parentElement;
        
        videoContainer.addEventListener('mousemove', (e) => this.handleMouseMove(e));
        videoContainer.addEventListener('mousedown', (e) => this.handleMouseDown(e));
        videoContainer.addEventListener('mouseup', (e) => this.handleMouseUp(e));
        videoContainer.addEventListener('wheel', (e) => this.handleMouseWheel(e), { passive: false });
        videoContainer.addEventListener('contextmenu', (e) => e.preventDefault());
        
        // Keyboard
        document.addEventListener('keydown', (e) => this.handleKeyDown(e));
        document.addEventListener('keyup', (e) => this.handleKeyUp(e));
        
        // Special key buttons
        document.querySelectorAll('.special-key-btn').forEach(btn => {
            btn.addEventListener('click', (e) => this.handleSpecialKey(e));
        });
        
        // Virtual mouse buttons
        document.getElementById('mouse-left')?.addEventListener('mousedown', () => {
            this.sendMouseClick(0, true);
        });
        document.getElementById('mouse-left')?.addEventListener('mouseup', () => {
            this.sendMouseClick(0, false);
        });
        
        document.getElementById('mouse-right')?.addEventListener('mousedown', () => {
            this.sendMouseClick(2, true);
        });
        document.getElementById('mouse-right')?.addEventListener('mouseup', () => {
            this.sendMouseClick(2, false);
        });
        
        document.getElementById('mouse-middle')?.addEventListener('mousedown', () => {
            this.sendMouseClick(1, true);
        });
        document.getElementById('mouse-middle')?.addEventListener('mouseup', () => {
            this.sendMouseClick(1, false);
        });
        
        // Text paste button
        document.getElementById('send-text-btn')?.addEventListener('click', () => {
            this.sendText();
        });
        
        document.getElementById('text-input')?.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                this.sendText();
            }
        });
        
        // Reconnect button
        document.getElementById('reconnect-btn')?.addEventListener('click', () => {
            this.reconnect();
        });
        
        // Video events
        this.videoElement.addEventListener('loadeddata', () => {
            console.log('[WebRTC] Video stream started');
            this.streamActive = true;
            this.updateConnectionStatus('connected');
            this.startLatencyMeasurement();
        });
        
        this.videoElement.addEventListener('error', (e) => {
            console.error('[WebRTC] Video error:', e);
            this.streamActive = false;
            this.updateConnectionStatus('error');
        });
        
        // Visibility change (pause/resume on tab switch)
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) {
                console.log('[WebRTC] Tab hidden, pausing stats');
            } else {
                console.log('[WebRTC] Tab visible, resuming stats');
            }
        });
    }
    
    async connect() {
        try {
            console.log('[WebRTC] Connecting to WebRTC service...');
            this.updateConnectionStatus('connecting');
            
            // Create peer connection
            this.peerConnection = new RTCPeerConnection({
                iceServers: this.config.iceServers
            });
            
            // Set up event handlers
            this.peerConnection.onicecandidate = (event) => {
                if (event.candidate) {
                    this.sendIceCandidate(event.candidate);
                }
            };
            
            this.peerConnection.ontrack = (event) => {
                console.log('[WebRTC] Received remote track:', event.track.kind);
                if (event.track.kind === 'video') {
                    this.videoElement.srcObject = event.streams[0];
                }
            };
            
            this.peerConnection.onconnectionstatechange = () => {
                console.log('[WebRTC] Connection state:', this.peerConnection.connectionState);
                this.updateConnectionStatus(this.peerConnection.connectionState);
                
                if (this.peerConnection.connectionState === 'failed' || 
                    this.peerConnection.connectionState === 'disconnected') {
                    this.handleDisconnect();
                }
            };
            
            this.peerConnection.oniceconnectionstatechange = () => {
                console.log('[WebRTC] ICE connection state:', this.peerConnection.iceConnectionState);
            };
            
            // Create data channel for HID input
            this.dataChannel = this.peerConnection.createDataChannel('hid-input', {
                ordered: false,  // Low latency, allow out-of-order
                maxRetransmits: 0  // No retransmits for real-time input
            });
            
            this.dataChannel.binaryType = 'arraybuffer';
            
            this.dataChannel.onopen = () => {
                console.log('[WebRTC] Data channel opened');
                this.updateConnectionStatus('data-channel-ready');
            };
            
            this.dataChannel.onclose = () => {
                console.log('[WebRTC] Data channel closed');
            };
            
            this.dataChannel.onerror = (error) => {
                console.error('[WebRTC] Data channel error:', error);
            };
            
            // Create offer
            const offer = await this.peerConnection.createOffer({
                offerToReceiveVideo: true,
                offerToReceiveAudio: false
            });
            
            await this.peerConnection.setLocalDescription(offer);
            
            // Send offer to server
            const response = await fetch('/api/webrtc/offer', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    sdp: offer.sdp,
                    type: offer.type
                })
            });
            
            if (!response.ok) {
                throw new Error(`Failed to send offer: ${response.statusText}`);
            }
            
            const data = await response.json();
            
            if (data.error) {
                throw new Error(data.error);
            }
            
            this.sessionId = data.session_id;
            
            // Set remote description (answer from server)
            await this.peerConnection.setRemoteDescription({
                type: 'answer',
                sdp: data.sdp
            });
            
            console.log('[WebRTC] Connection established, session ID:', this.sessionId);
            this.reconnectAttempts = 0;
            
        } catch (error) {
            console.error('[WebRTC] Connection failed:', error);
            this.updateConnectionStatus('error');
            this.handleDisconnect();
        }
    }
    
    async sendIceCandidate(candidate) {
        if (!this.sessionId) {
            console.warn('[WebRTC] Cannot send ICE candidate without session ID');
            return;
        }
        
        try {
            const response = await fetch('/api/webrtc/ice-candidate', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    session_id: this.sessionId,
                    candidate: candidate.candidate,
                    sdpMid: candidate.sdpMid,
                    sdpMLineIndex: candidate.sdpMLineIndex
                })
            });
            
            if (!response.ok) {
                console.error('[WebRTC] Failed to send ICE candidate');
            }
        } catch (error) {
            console.error('[WebRTC] Error sending ICE candidate:', error);
        }
    }
    
    disconnect() {
        console.log('[WebRTC] Disconnecting...');
        
        if (this.dataChannel) {
            this.dataChannel.close();
            this.dataChannel = null;
        }
        
        if (this.peerConnection) {
            this.peerConnection.close();
            this.peerConnection = null;
        }
        
        this.sessionId = null;
        this.streamActive = false;
        this.updateConnectionStatus('disconnected');
    }
    
    async reconnect() {
        console.log('[WebRTC] Reconnecting...');
        this.disconnect();
        this.reconnectAttempts = 0;
        await this.connect();
    }
    
    handleDisconnect() {
        if (this.reconnectAttempts < this.config.maxReconnectAttempts) {
            this.reconnectAttempts++;
            console.log(`[WebRTC] Reconnect attempt ${this.reconnectAttempts}/${this.config.maxReconnectAttempts}`);
            
            setTimeout(() => {
                this.reconnect();
            }, this.config.reconnectDelay);
        } else {
            console.error('[WebRTC] Max reconnect attempts reached');
            this.updateConnectionStatus('failed');
        }
    }
    
    updateConnectionStatus(state) {
        this.signalingState = state;
        
        const statusElement = document.getElementById('connection-status');
        const statusText = document.getElementById('status-text');
        
        if (!statusElement || !statusText) return;
        
        const statusMap = {
            'disconnected': { text: 'Disconnected', class: 'status-error' },
            'connecting': { text: 'Connecting...', class: 'status-warning' },
            'connected': { text: 'Connected', class: 'status-ok' },
            'data-channel-ready': { text: 'Ready', class: 'status-ok' },
            'failed': { text: 'Connection Failed', class: 'status-error' },
            'error': { text: 'Error', class: 'status-error' }
        };
        
        const status = statusMap[state] || statusMap['disconnected'];
        
        statusElement.className = `status-indicator ${status.class}`;
        statusText.textContent = status.text;
    }
    
    // === Input Handling ===
    
    handleMouseMove(e) {
        if (!this.dataChannel || this.dataChannel.readyState !== 'open') {
            return;
        }
        
        const rect = e.target.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;
        
        const deltaX = Math.round(x - this.lastMouseX);
        const deltaY = Math.round(y - this.lastMouseY);
        
        this.lastMouseX = x;
        this.lastMouseY = y;
        
        if (deltaX !== 0 || deltaY !== 0) {
            this.sendMouseMove(deltaX, deltaY);
        }
    }
    
    handleMouseDown(e) {
        e.preventDefault();
        
        if (!this.dataChannel || this.dataChannel.readyState !== 'open') {
            return;
        }
        
        this.sendMouseClick(e.button, true);
    }
    
    handleMouseUp(e) {
        e.preventDefault();
        
        if (!this.dataChannel || this.dataChannel.readyState !== 'open') {
            return;
        }
        
        this.sendMouseClick(e.button, false);
    }
    
    handleMouseWheel(e) {
        e.preventDefault();
        
        if (!this.dataChannel || this.dataChannel.readyState !== 'open') {
            return;
        }
        
        this.sendMouseWheel(e.deltaX, e.deltaY);
    }
    
    handleKeyDown(e) {
        // Don't capture keys when typing in input fields
        if (this.isInputElement(e.target)) {
            return;
        }
        
        e.preventDefault();
        
        if (!this.dataChannel || this.dataChannel.readyState !== 'open') {
            return;
        }
        
        // Prevent key repeat
        const keyId = `${e.code}-${e.keyCode}`;
        if (this.keysPressed.has(keyId)) {
            return;
        }
        this.keysPressed.add(keyId);
        
        const usbKeycode = this.keyCodeToUSB(e.code, e.keyCode);
        if (usbKeycode !== null) {
            this.sendKeyboard(usbKeycode, true);
        }
    }
    
    handleKeyUp(e) {
        if (this.isInputElement(e.target)) {
            return;
        }
        
        e.preventDefault();
        
        if (!this.dataChannel || this.dataChannel.readyState !== 'open') {
            return;
        }
        
        const keyId = `${e.code}-${e.keyCode}`;
        this.keysPressed.delete(keyId);
        
        const usbKeycode = this.keyCodeToUSB(e.code, e.keyCode);
        if (usbKeycode !== null) {
            this.sendKeyboard(usbKeycode, false);
        }
    }
    
    isInputElement(element) {
        return element.tagName === 'INPUT' || 
               element.tagName === 'TEXTAREA' || 
               element.tagName === 'SELECT';
    }
    
    handleSpecialKey(e) {
        const btn = e.currentTarget;
        
        if (btn.dataset.action === 'ctrl-alt-del') {
            this.sendCtrlAltDel();
        } else if (btn.dataset.key) {
            const keycode = parseInt(btn.dataset.key, 16);
            this.sendKeyCode(keycode);
        }
    }
    
    sendText() {
        const input = document.getElementById('text-input');
        if (!input || !input.value) return;
        
        const text = input.value;
        
        // Send each character as a key press/release sequence
        for (const char of text) {
            const keycode = this.charToUSBKeycode(char);
            if (keycode) {
                this.sendKeyboard(keycode.code, true);
                setTimeout(() => {
                    this.sendKeyboard(keycode.code, false);
                }, 10);
            }
        }
        
        input.value = '';
    }
    
    // === Binary Protocol Methods ===
    
    /**
     * Send keyboard event
     * Binary format: [0x01][keycode:1][pressed:1]
     */
    sendKeyboard(keycode, pressed) {
        if (!this.dataChannel || this.dataChannel.readyState !== 'open') {
            return;
        }
        
        const buffer = new Uint8Array(3);
        buffer[0] = 0x01;  // Keyboard message type
        buffer[1] = keycode & 0xFF;
        buffer[2] = pressed ? 1 : 0;
        
        try {
            this.dataChannel.send(buffer);
        } catch (error) {
            console.error('[WebRTC] Failed to send keyboard event:', error);
        }
    }
    
    /**
     * Send mouse move event
     * Binary format: [0x02][dx:2][dy:2]
     */
    sendMouseMove(dx, dy) {
        if (!this.dataChannel || this.dataChannel.readyState !== 'open') {
            return;
        }
        
        const buffer = new Uint8Array(5);
        const view = new DataView(buffer.buffer);
        
        view.setUint8(0, 0x02);  // Mouse move message type
        view.setInt16(1, dx, false);  // Big-endian (network byte order)
        view.setInt16(3, dy, false);
        
        try {
            this.dataChannel.send(buffer);
        } catch (error) {
            console.error('[WebRTC] Failed to send mouse move:', error);
        }
    }
    
    /**
     * Send mouse click event
     * Binary format: [0x03][button:1][pressed:1]
     * button: 0=left, 1=middle, 2=right
     */
    sendMouseClick(button, pressed) {
        if (!this.dataChannel || this.dataChannel.readyState !== 'open') {
            return;
        }
        
        const buffer = new Uint8Array(3);
        buffer[0] = 0x03;  // Mouse click message type
        buffer[1] = button & 0xFF;
        buffer[2] = pressed ? 1 : 0;
        
        try {
            this.dataChannel.send(buffer);
        } catch (error) {
            console.error('[WebRTC] Failed to send mouse click:', error);
        }
    }
    
    /**
     * Send mouse wheel event
     * Binary format: [0x04][delta_x:2][delta_y:2]
     */
    sendMouseWheel(deltaX, deltaY) {
        if (!this.dataChannel || this.dataChannel.readyState !== 'open') {
            return;
        }
        
        const buffer = new Uint8Array(5);
        const view = new DataView(buffer.buffer);
        
        view.setUint8(0, 0x04);  // Mouse wheel message type
        view.setInt16(1, Math.round(deltaX), false);
        view.setInt16(3, Math.round(deltaY), false);
        
        try {
            this.dataChannel.send(buffer);
        } catch (error) {
            console.error('[WebRTC] Failed to send mouse wheel:', error);
        }
    }
    
    sendKeyCode(keycode) {
        this.sendKeyboard(keycode, true);
        setTimeout(() => {
            this.sendKeyboard(keycode, false);
        }, 100);
    }
    
    sendCtrlAltDel() {
        // USB HID keycodes: Ctrl=0xE0, Alt=0xE2, Del=0x4C
        this.sendKeyboard(0xE0, true);  // Ctrl down
        this.sendKeyboard(0xE2, true);  // Alt down
        this.sendKeyboard(0x4C, true);  // Del down
        
        setTimeout(() => {
            this.sendKeyboard(0x4C, false);  // Del up
            this.sendKeyboard(0xE2, false);  // Alt up
            this.sendKeyboard(0xE0, false);  // Ctrl up
        }, 100);
    }
    
    // === Keycode Mapping ===
    
    /**
     * Convert JavaScript KeyboardEvent.code to USB HID keycode
     */
    keyCodeToUSB(code, keyCode) {
        // Modern browsers provide 'code' (physical key)
        // Fallback to 'keyCode' for older browsers
        
        const codeMap = {
            // Letters
            'KeyA': 0x04, 'KeyB': 0x05, 'KeyC': 0x06, 'KeyD': 0x07,
            'KeyE': 0x08, 'KeyF': 0x09, 'KeyG': 0x0A, 'KeyH': 0x0B,
            'KeyI': 0x0C, 'KeyJ': 0x0D, 'KeyK': 0x0E, 'KeyL': 0x0F,
            'KeyM': 0x10, 'KeyN': 0x11, 'KeyO': 0x12, 'KeyP': 0x13,
            'KeyQ': 0x14, 'KeyR': 0x15, 'KeyS': 0x16, 'KeyT': 0x17,
            'KeyU': 0x18, 'KeyV': 0x19, 'KeyW': 0x1A, 'KeyX': 0x1B,
            'KeyY': 0x1C, 'KeyZ': 0x1D,
            
            // Numbers
            'Digit1': 0x1E, 'Digit2': 0x1F, 'Digit3': 0x20, 'Digit4': 0x21,
            'Digit5': 0x22, 'Digit6': 0x23, 'Digit7': 0x24, 'Digit8': 0x25,
            'Digit9': 0x26, 'Digit0': 0x27,
            
            // Special keys
            'Enter': 0x28, 'Escape': 0x29, 'Backspace': 0x2A, 'Tab': 0x2B,
            'Space': 0x2C, 'Minus': 0x2D, 'Equal': 0x2E,
            'BracketLeft': 0x2F, 'BracketRight': 0x30, 'Backslash': 0x31,
            'Semicolon': 0x33, 'Quote': 0x34, 'Backquote': 0x35,
            'Comma': 0x36, 'Period': 0x37, 'Slash': 0x38,
            'CapsLock': 0x39,
            
            // Function keys
            'F1': 0x3A, 'F2': 0x3B, 'F3': 0x3C, 'F4': 0x3D,
            'F5': 0x3E, 'F6': 0x3F, 'F7': 0x40, 'F8': 0x41,
            'F9': 0x42, 'F10': 0x43, 'F11': 0x44, 'F12': 0x45,
            
            // Navigation
            'PrintScreen': 0x46, 'ScrollLock': 0x47, 'Pause': 0x48,
            'Insert': 0x49, 'Home': 0x4A, 'PageUp': 0x4B,
            'Delete': 0x4C, 'End': 0x4D, 'PageDown': 0x4E,
            'ArrowRight': 0x4F, 'ArrowLeft': 0x50, 'ArrowDown': 0x51, 'ArrowUp': 0x52,
            
            // Numpad
            'NumLock': 0x53,
            'NumpadDivide': 0x54, 'NumpadMultiply': 0x55, 'NumpadSubtract': 0x56,
            'NumpadAdd': 0x57, 'NumpadEnter': 0x58,
            'Numpad1': 0x59, 'Numpad2': 0x5A, 'Numpad3': 0x5B,
            'Numpad4': 0x5C, 'Numpad5': 0x5D, 'Numpad6': 0x5E,
            'Numpad7': 0x5F, 'Numpad8': 0x60, 'Numpad9': 0x61,
            'Numpad0': 0x62, 'NumpadDecimal': 0x63,
            
            // Modifiers
            'ControlLeft': 0xE0, 'ShiftLeft': 0xE1, 'AltLeft': 0xE2, 'MetaLeft': 0xE3,
            'ControlRight': 0xE4, 'ShiftRight': 0xE5, 'AltRight': 0xE6, 'MetaRight': 0xE7
        };
        
        if (code && codeMap[code] !== undefined) {
            return codeMap[code];
        }
        
        // Fallback for older browsers using keyCode
        const keyCodeMap = {
            8: 0x2A,   // Backspace
            9: 0x2B,   // Tab
            13: 0x28,  // Enter
            27: 0x29,  // Escape
            32: 0x2C,  // Space
            37: 0x50,  // Left
            38: 0x52,  // Up
            39: 0x4F,  // Right
            40: 0x51,  // Down
            46: 0x4C,  // Delete
            112: 0x3A, 113: 0x3B, 114: 0x3C, 115: 0x3D,  // F1-F4
            116: 0x3E, 117: 0x3F, 118: 0x40, 119: 0x41,  // F5-F8
            120: 0x42, 121: 0x43, 122: 0x44, 123: 0x45   // F9-F12
        };
        
        if (keyCodeMap[keyCode] !== undefined) {
            return keyCodeMap[keyCode];
        }
        
        // A-Z
        if (keyCode >= 65 && keyCode <= 90) {
            return 0x04 + (keyCode - 65);
        }
        
        // 0-9
        if (keyCode >= 48 && keyCode <= 57) {
            return keyCode === 48 ? 0x27 : 0x1E + (keyCode - 49);
        }
        
        console.warn('[WebRTC] Unknown key:', code, keyCode);
        return null;
    }
    
    /**
     * Convert character to USB HID keycode with shift modifier
     */
    charToUSBKeycode(char) {
        const charMap = {
            'a': { code: 0x04, shift: false }, 'b': { code: 0x05, shift: false },
            'c': { code: 0x06, shift: false }, 'd': { code: 0x07, shift: false },
            'e': { code: 0x08, shift: false }, 'f': { code: 0x09, shift: false },
            'g': { code: 0x0A, shift: false }, 'h': { code: 0x0B, shift: false },
            'i': { code: 0x0C, shift: false }, 'j': { code: 0x0D, shift: false },
            'k': { code: 0x0E, shift: false }, 'l': { code: 0x0F, shift: false },
            'm': { code: 0x10, shift: false }, 'n': { code: 0x11, shift: false },
            'o': { code: 0x12, shift: false }, 'p': { code: 0x13, shift: false },
            'q': { code: 0x14, shift: false }, 'r': { code: 0x15, shift: false },
            's': { code: 0x16, shift: false }, 't': { code: 0x17, shift: false },
            'u': { code: 0x18, shift: false }, 'v': { code: 0x19, shift: false },
            'w': { code: 0x1A, shift: false }, 'x': { code: 0x1B, shift: false },
            'y': { code: 0x1C, shift: false }, 'z': { code: 0x1D, shift: false },
            
            'A': { code: 0x04, shift: true }, 'B': { code: 0x05, shift: true },
            'C': { code: 0x06, shift: true }, 'D': { code: 0x07, shift: true },
            'E': { code: 0x08, shift: true }, 'F': { code: 0x09, shift: true },
            'G': { code: 0x0A, shift: true }, 'H': { code: 0x0B, shift: true },
            'I': { code: 0x0C, shift: true }, 'J': { code: 0x0D, shift: true },
            'K': { code: 0x0E, shift: true }, 'L': { code: 0x0F, shift: true },
            'M': { code: 0x10, shift: true }, 'N': { code: 0x11, shift: true },
            'O': { code: 0x12, shift: true }, 'P': { code: 0x13, shift: true },
            'Q': { code: 0x14, shift: true }, 'R': { code: 0x15, shift: true },
            'S': { code: 0x16, shift: true }, 'T': { code: 0x17, shift: true },
            'U': { code: 0x18, shift: true }, 'V': { code: 0x19, shift: true },
            'W': { code: 0x1A, shift: true }, 'X': { code: 0x1B, shift: true },
            'Y': { code: 0x1C, shift: true }, 'Z': { code: 0x1D, shift: true },
            
            '1': { code: 0x1E, shift: false }, '2': { code: 0x1F, shift: false },
            '3': { code: 0x20, shift: false }, '4': { code: 0x21, shift: false },
            '5': { code: 0x22, shift: false }, '6': { code: 0x23, shift: false },
            '7': { code: 0x24, shift: false }, '8': { code: 0x25, shift: false },
            '9': { code: 0x26, shift: false }, '0': { code: 0x27, shift: false },
            
            '!': { code: 0x1E, shift: true }, '@': { code: 0x1F, shift: true },
            '#': { code: 0x20, shift: true }, '$': { code: 0x21, shift: true },
            '%': { code: 0x22, shift: true }, '^': { code: 0x23, shift: true },
            '&': { code: 0x24, shift: true }, '*': { code: 0x25, shift: true },
            '(': { code: 0x26, shift: true }, ')': { code: 0x27, shift: true },
            
            ' ': { code: 0x2C, shift: false }, '-': { code: 0x2D, shift: false },
            '=': { code: 0x2E, shift: false }, '[': { code: 0x2F, shift: false },
            ']': { code: 0x30, shift: false }, '\\': { code: 0x31, shift: false },
            ';': { code: 0x33, shift: false }, "'": { code: 0x34, shift: false },
            '`': { code: 0x35, shift: false }, ',': { code: 0x36, shift: false },
            '.': { code: 0x37, shift: false }, '/': { code: 0x38, shift: false },
            
            '_': { code: 0x2D, shift: true }, '+': { code: 0x2E, shift: true },
            '{': { code: 0x2F, shift: true }, '}': { code: 0x30, shift: true },
            '|': { code: 0x31, shift: true }, ':': { code: 0x33, shift: true },
            '"': { code: 0x34, shift: true }, '~': { code: 0x35, shift: true },
            '<': { code: 0x36, shift: true }, '>': { code: 0x37, shift: true },
            '?': { code: 0x38, shift: true },
            
            '\n': { code: 0x28, shift: false },  // Enter
            '\t': { code: 0x2B, shift: false }   // Tab
        };
        
        return charMap[char] || null;
    }
    
    // === Latency Measurement ===
    
    startLatencyMeasurement() {
        if (!this.videoElement || !('requestVideoFrameCallback' in this.videoElement)) {
            console.warn('[WebRTC] requestVideoFrameCallback not supported, latency measurement disabled');
            return;
        }
        
        const measureFrame = (now, metadata) => {
            if (!this.streamActive) return;
            
            // Calculate latency: current time - frame capture time
            // metadata.captureTime is in milliseconds since epoch (from server)
            // metadata.receiveTime is when frame was received by browser
            // metadata.presentationTime is when frame will be presented
            
            if (metadata.receiveTime && metadata.captureTime) {
                const latencyMs = metadata.receiveTime - metadata.captureTime;
                
                if (latencyMs > 0 && latencyMs < 5000) {  // Sanity check
                    this.updateLatencyStats(latencyMs);
                }
            }
            
            // Request next frame callback
            this.videoElement.requestVideoFrameCallback(measureFrame);
        };
        
        this.videoElement.requestVideoFrameCallback(measureFrame);
    }
    
    updateLatencyStats(latencyMs) {
        this.latencyStats.current = latencyMs;
        this.latencyStats.min = Math.min(this.latencyStats.min, latencyMs);
        this.latencyStats.max = Math.max(this.latencyStats.max, latencyMs);
        
        this.latencyStats.samples.push(latencyMs);
        
        // Keep only recent samples
        if (this.latencyStats.samples.length > this.latencySampleSize) {
            this.latencyStats.samples.shift();
        }
        
        // Calculate average
        const sum = this.latencyStats.samples.reduce((a, b) => a + b, 0);
        this.latencyStats.avg = sum / this.latencyStats.samples.length;
        
        // Calculate p95
        if (this.latencyStats.samples.length >= 20) {
            const sorted = [...this.latencyStats.samples].sort((a, b) => a - b);
            const p95Index = Math.floor(sorted.length * 0.95);
            this.latencyStats.p95 = sorted[p95Index];
        }
        
        // Update UI
        this.updateLatencyDisplay();
    }
    
    updateLatencyDisplay() {
        const latencyElement = document.getElementById('latency-stats');
        if (!latencyElement) return;
        
        const { current, min, max, avg, p95 } = this.latencyStats;
        
        latencyElement.textContent = 
            `Latency: ${current.toFixed(1)}ms | ` +
            `Avg: ${avg.toFixed(1)}ms | ` +
            `p95: ${p95.toFixed(1)}ms | ` +
            `Min/Max: ${min.toFixed(1)}/${max.toFixed(1)}ms`;
        
        // Color code based on latency
        latencyElement.className = current < 50 ? 'latency-good' : 
                                   current < 100 ? 'latency-ok' : 'latency-poor';
    }
    
    // === Status Updates ===
    
    startStatusCheck() {
        this.statusCheckInterval = setInterval(async () => {
            await this.updateStatus();
        }, 5000);
        
        this.updateStatus();
    }
    
    async updateStatus() {
        if (!this.sessionId) return;
        
        try {
            const response = await fetch(`/api/webrtc/stats?session_id=${this.sessionId}`);
            
            if (!response.ok) return;
            
            const stats = await response.json();
            
            // Update encoder info
            const encoderElement = document.getElementById('encoder-info');
            if (encoderElement && stats.encoder) {
                encoderElement.textContent = `Encoder: ${stats.encoder.name} (${stats.encoder.type})`;
            }
            
            // Update bitrate
            const bitrateElement = document.getElementById('bitrate-info');
            if (bitrateElement && stats.bitrate_kbps) {
                bitrateElement.textContent = `Bitrate: ${stats.bitrate_kbps} kbps`;
            }
            
            // Update resolution/FPS
            const resolutionElement = document.getElementById('resolution-info');
            if (resolutionElement && stats.resolution) {
                resolutionElement.textContent = 
                    `Resolution: ${stats.resolution.width}x${stats.resolution.height}@${stats.fps || 30}fps`;
            }
            
        } catch (error) {
            console.error('[WebRTC] Failed to update status:', error);
        }
    }
    
    // === Cleanup ===
    
    destroy() {
        if (this.statusCheckInterval) {
            clearInterval(this.statusCheckInterval);
        }
        
        this.disconnect();
    }
});

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    window.webrtcConsole = new WebRTCConsole();
});
