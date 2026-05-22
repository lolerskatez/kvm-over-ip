class KVMConsole {
    constructor() {
        this.mouseX = 0;
        this.mouseY = 0;
        this.lastMouseX = 0;
        this.lastMouseY = 0;
        this.config = {};
        this.statusCheckInterval = null;
        
        this.init();
    }
    
    async init() {
        this.setupEventListeners();
        await this.loadConfig();
        await this.updateStatus();
        this.startStatusCheck();
    }
    
    setupEventListeners() {
        const videoStream = document.getElementById('video-stream');
        
        videoStream.addEventListener('mousemove', (e) => this.handleMouseMove(e));
        videoStream.addEventListener('mousedown', (e) => this.handleMouseDown(e));
        videoStream.addEventListener('mouseup', (e) => this.handleMouseUp(e));
        
        document.addEventListener('keydown', (e) => this.handleKeyDown(e));
        document.addEventListener('keyup', (e) => this.handleKeyUp(e));
        
        document.querySelectorAll('.special-key-btn').forEach(btn => {
            btn.addEventListener('click', (e) => this.handleSpecialKey(e));
        });
        
        document.getElementById('mouse-left').addEventListener('mousedown', () => {
            this.sendMouseClick('left', true);
        });
        document.getElementById('mouse-left').addEventListener('mouseup', () => {
            this.sendMouseClick('left', false);
        });
        
        document.getElementById('mouse-right').addEventListener('mousedown', () => {
            this.sendMouseClick('right', true);
        });
        document.getElementById('mouse-right').addEventListener('mouseup', () => {
            this.sendMouseClick('right', false);
        });
        
        document.getElementById('mouse-middle').addEventListener('mousedown', () => {
            this.sendMouseClick('middle', true);
        });
        document.getElementById('mouse-middle').addEventListener('mouseup', () => {
            this.sendMouseClick('middle', false);
        });
        
        document.getElementById('send-text-btn').addEventListener('click', () => {
            this.sendText();
        });
        
        document.getElementById('text-input').addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                this.sendText();
            }
        });
        
        document.getElementById('config-btn').addEventListener('click', () => {
            this.showConfigModal();
        });
        
        document.querySelector('.modal-close').addEventListener('click', () => {
            this.hideConfigModal();
        });
        
        document.getElementById('config-cancel-btn').addEventListener('click', () => {
            this.hideConfigModal();
        });
        
        document.getElementById('config-save-btn').addEventListener('click', () => {
            this.saveConfig();
        });
        
        document.getElementById('video-stream').addEventListener('load', () => {
            document.querySelector('.video-overlay').classList.remove('loading');
        });
        
        document.getElementById('video-stream').addEventListener('error', () => {
            document.querySelector('.video-overlay').classList.add('loading');
        });
    }
    
    async loadConfig() {
        try {
            const response = await fetch('/api/config');
            if (response.ok) {
                this.config = await response.json();
                this.updateConfigDisplay();
            }
        } catch (error) {
            console.error('Failed to load config:', error);
        }
    }
    
    updateConfigDisplay() {
        document.getElementById('resolution-info').textContent = 
            `Resolution: ${this.config.resolution || 'N/A'}`;
        document.getElementById('fps-info').textContent = 
            `FPS: ${this.config.framerate || 'N/A'}`;
    }
    
    showConfigModal() {
        const modal = document.getElementById('config-modal');
        document.getElementById('config-resolution').value = this.config.resolution || '';
        document.getElementById('config-framerate').value = this.config.framerate || '';
        document.getElementById('config-bitrate').value = this.config.bitrate || '';
        document.getElementById('config-video-device').value = this.config.video_device || '';
        document.getElementById('config-hid-device').value = this.config.hid_device || '';
        modal.classList.remove('hidden');
    }
    
    hideConfigModal() {
        document.getElementById('config-modal').classList.add('hidden');
    }
    
    async saveConfig() {
        const newConfig = {
            resolution: document.getElementById('config-resolution').value,
            framerate: parseInt(document.getElementById('config-framerate').value),
            bitrate: document.getElementById('config-bitrate').value,
            video_device: document.getElementById('config-video-device').value,
            hid_device: document.getElementById('config-hid-device').value
        };
        
        try {
            const response = await fetch('/api/config', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(newConfig)
            });
            
            if (response.ok) {
                const result = await response.json();
                this.config = result.config;
                this.updateConfigDisplay();
                this.hideConfigModal();
                alert('Configuration saved. Changes will apply on next stream restart.');
            } else {
                alert('Failed to save configuration');
            }
        } catch (error) {
            console.error('Failed to save config:', error);
            alert('Error saving configuration');
        }
    }
    
    handleMouseMove(e) {
        const rect = e.target.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;
        
        const deltaX = x - this.lastMouseX;
        const deltaY = y - this.lastMouseY;
        
        this.lastMouseX = x;
        this.lastMouseY = y;
        
        if (deltaX !== 0 || deltaY !== 0) {
            this.sendMouseMove(deltaX, deltaY);
        }
    }
    
    handleMouseDown(e) {
        const buttonMap = {
            0: 'left',
            1: 'middle',
            2: 'right'
        };
        
        const button = buttonMap[e.button];
        if (button) {
            this.sendMouseClick(button, true);
        }
    }
    
    handleMouseUp(e) {
        const buttonMap = {
            0: 'left',
            1: 'middle',
            2: 'right'
        };
        
        const button = buttonMap[e.button];
        if (button) {
            this.sendMouseClick(button, false);
        }
    }
    
    handleKeyDown(e) {
        if (this.isInputElement(e.target)) {
            return;
        }
        
        e.preventDefault();
        this.sendKey(e.keyCode, e.ctrlKey, e.shiftKey, e.altKey);
    }
    
    handleKeyUp(e) {
        if (this.isInputElement(e.target)) {
            return;
        }
        
        e.preventDefault();
    }
    
    isInputElement(element) {
        return element.tagName === 'INPUT' || element.tagName === 'TEXTAREA';
    }
    
    handleSpecialKey(e) {
        const btn = e.target;
        
        if (btn.dataset.action === 'ctrl-alt-del') {
            this.sendCtrlAltDel();
        } else if (btn.dataset.key) {
            const keycode = parseInt(btn.dataset.key, 16);
            this.sendKeyCode(keycode);
        }
    }
    
    keyCodeToUSB(keyCode) {
        const keyMap = {
            8: 0x2A,
            9: 0x2B,
            13: 0x28,
            27: 0x29,
            32: 0x2C,
            37: 0x50,
            38: 0x52,
            39: 0x4F,
            40: 0x51,
            65: 0x04,
            66: 0x05,
            67: 0x06,
            68: 0x07,
            69: 0x08,
            70: 0x09,
            71: 0x0A,
            72: 0x0B,
            73: 0x0C,
            74: 0x0D,
            75: 0x0E,
            76: 0x0F,
            77: 0x10,
            78: 0x11,
            79: 0x12,
            80: 0x13,
            81: 0x14,
            82: 0x15,
            83: 0x16,
            84: 0x17,
            85: 0x18,
            86: 0x19,
            87: 0x1A,
            88: 0x1B,
            89: 0x1C,
            90: 0x1D,
            48: 0x27,
            49: 0x1E,
            50: 0x1F,
            51: 0x20,
            52: 0x21,
            53: 0x22,
            54: 0x23,
            55: 0x24,
            56: 0x25,
            57: 0x26,
            112: 0x3A,
            113: 0x3B,
            114: 0x3C,
            115: 0x3D,
            116: 0x3E,
            117: 0x3F,
            118: 0x40,
            119: 0x41,
            120: 0x42,
            121: 0x43,
            122: 0x44,
            123: 0x45,
            17: 0x00,
            16: 0x00,
            18: 0x00
        };
        
        return keyMap[keyCode] || null;
    }
    
    async sendKey(keyCode, ctrl, shift, alt) {
        const usbKeyCode = this.keyCodeToUSB(keyCode);
        if (!usbKeyCode) return;
        
        let modifiers = 0;
        if (ctrl) modifiers |= 0x01;
        if (shift) modifiers |= 0x02;
        if (alt) modifiers |= 0x04;
        
        try {
            await fetch('/api/keyboard', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    action: 'key_with_modifier',
                    keycode: usbKeyCode,
                    modifiers: modifiers
                })
            });
        } catch (error) {
            console.error('Failed to send key:', error);
        }
    }
    
    async sendKeyCode(keycode) {
        try {
            await fetch('/api/keyboard', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    action: 'key',
                    keycode: keycode,
                    pressed: true
                })
            });
        } catch (error) {
            console.error('Failed to send key:', error);
        }
    }
    
    async sendMouseMove(x, y) {
        try {
            await fetch('/api/mouse', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    action: 'move',
                    x: x,
                    y: y
                })
            });
        } catch (error) {
            console.error('Failed to send mouse move:', error);
        }
    }
    
    async sendMouseClick(button, pressed) {
        try {
            await fetch('/api/mouse', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    action: 'click',
                    button: button,
                    pressed: pressed
                })
            });
        } catch (error) {
            console.error('Failed to send mouse click:', error);
        }
    }
    
    async sendCtrlAltDel() {
        try {
            await fetch('/api/keyboard', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    action: 'ctrl_alt_del'
                })
            });
        } catch (error) {
            console.error('Failed to send Ctrl+Alt+Del:', error);
        }
    }
    
    async sendText() {
        const input = document.getElementById('text-input');
        const text = input.value.trim();
        
        if (!text) return;
        
        try {
            await fetch('/api/keyboard', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    action: 'text',
                    text: text
                })
            });
            
            input.value = '';
        } catch (error) {
            console.error('Failed to send text:', error);
        }
    }
    
    async updateStatus() {
        try {
            const response = await fetch('/api/status');
            if (response.ok) {
                const status = await response.json();
                
                const hidDot = document.getElementById('status-hid');
                const videoDot = document.getElementById('status-video');
                const userInfo = document.getElementById('user-info');
                
                hidDot.classList.toggle('connected', status.hid_connected);
                hidDot.classList.toggle('disconnected', !status.hid_connected);
                hidDot.title = status.hid_connected ? 'HID Connected' : 'HID Disconnected';
                
                videoDot.classList.toggle('connected', status.video_running);
                videoDot.classList.toggle('disconnected', !status.video_running);
                videoDot.title = status.video_running ? 'Video Streaming' : 'Video Stopped';
                
                userInfo.textContent = `Logged in as: ${status.user}`;
            }
        } catch (error) {
            console.error('Failed to update status:', error);
        }
    }
    
    startStatusCheck() {
        this.statusCheckInterval = setInterval(() => {
            this.updateStatus();
        }, 5000);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    new KVMConsole();
});
