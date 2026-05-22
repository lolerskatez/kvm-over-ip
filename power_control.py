import os
import time
import threading
import subprocess
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


class PowerControlManager:
    """
    Power control system for target machines.
    Supports GPIO relays for power button emulation and optional IPMI.
    """
    
    # GPIO pin mappings (configurable)
    GPIO_POWER_BUTTON = 17    # Power button pin
    GPIO_RESET_BUTTON = 27    # Reset button pin
    GPIO_POWER_LED = 22       # Power status LED (input)
    
    # Power state durations (milliseconds)
    POWER_BUTTON_PRESS = 500    # Normal power button press
    POWER_BUTTON_HOLD = 10000   # Force power off (hold for 10 seconds)
    
    def __init__(self, enable_gpio=True, enable_ipmi=False, ipmi_host=None):
        """
        Initialize power control.
        
        Args:
            enable_gpio: Enable GPIO-based control (default True)
            enable_ipmi: Enable IPMI control for IPMI-capable targets
            ipmi_host: IPMI target hostname/IP (for IPMI mode)
        """
        self.enable_gpio = enable_gpio
        self.enable_ipmi = enable_ipmi
        self.ipmi_host = ipmi_host
        self.gpio_available = False
        self.ipmi_available = False
        self.power_state = None  # 'on', 'off', 'unknown'
        self.lock = threading.Lock()
        
        self._init_gpio()
        if enable_ipmi and ipmi_host:
            self._init_ipmi()
    
    def _init_gpio(self):
        """Initialize GPIO interface."""
        if not self.enable_gpio:
            return
        
        try:
            # Try gpiozero first (preferred)
            import gpiozero
            self.gpio_lib = 'gpiozero'
            self.gpio_available = True
            logger.info("GPIO initialized with gpiozero")
        except ImportError:
            try:
                # Fallback to RPi.GPIO
                import RPi.GPIO as GPIO
                self.gpio_lib = 'rpi_gpio'
                self.gpio_available = True
                GPIO.setmode(GPIO.BCM)
                logger.info("GPIO initialized with RPi.GPIO")
            except ImportError:
                logger.warning("GPIO library not available (gpiozero or RPi.GPIO)")
                self.gpio_available = False
    
    def _init_ipmi(self):
        """Initialize IPMI interface."""
        if not self.enable_ipmi or not self.ipmi_host:
            return
        
        try:
            # Test IPMI connectivity
            result = subprocess.run(
                ['ipmitool', '-H', self.ipmi_host, 'power', 'status'],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                self.ipmi_available = True
                logger.info(f"IPMI initialized for {self.ipmi_host}")
            else:
                logger.warning(f"IPMI test failed: {result.stderr.decode()}")
        except Exception as e:
            logger.warning(f"IPMI not available: {e}")
    
    def power_on(self):
        """
        Power on the target machine.
        
        Returns:
            dict with status
        """
        with self.lock:
            try:
                if self.ipmi_available:
                    return self._power_on_ipmi()
                elif self.gpio_available:
                    return self._power_on_gpio()
                else:
                    return {
                        'status': 'error',
                        'error': 'No power control method available'
                    }
            except Exception as e:
                logger.error(f"Power on failed: {e}")
                return {'status': 'error', 'error': str(e)}
    
    def power_off(self, force=False):
        """
        Power off the target machine.
        
        Args:
            force: Force shutdown (hold button for 10s) if True
        
        Returns:
            dict with status
        """
        with self.lock:
            try:
                if self.ipmi_available:
                    return self._power_off_ipmi(force)
                elif self.gpio_available:
                    return self._power_off_gpio(force)
                else:
                    return {
                        'status': 'error',
                        'error': 'No power control method available'
                    }
            except Exception as e:
                logger.error(f"Power off failed: {e}")
                return {'status': 'error', 'error': str(e)}
    
    def power_reset(self):
        """
        Reset (reboot) the target machine via reset button.
        
        Returns:
            dict with status
        """
        with self.lock:
            try:
                if self.ipmi_available:
                    return self._power_reset_ipmi()
                elif self.gpio_available:
                    return self._power_reset_gpio()
                else:
                    return {
                        'status': 'error',
                        'error': 'No power control method available'
                    }
            except Exception as e:
                logger.error(f"Power reset failed: {e}")
                return {'status': 'error', 'error': str(e)}
    
    def power_cycle(self):
        """
        Complete power cycle: off → wait → on.
        
        Returns:
            dict with status
        """
        with self.lock:
            try:
                if self.ipmi_available:
                    return self._power_cycle_ipmi()
                elif self.gpio_available:
                    return self._power_cycle_gpio()
                else:
                    return {
                        'status': 'error',
                        'error': 'No power control method available'
                    }
            except Exception as e:
                logger.error(f"Power cycle failed: {e}")
                return {'status': 'error', 'error': str(e)}
    
    def get_power_status(self):
        """
        Get current power status of target machine.
        
        Returns:
            dict with status ('on', 'off', 'unknown') and detection method
        """
        try:
            if self.ipmi_available:
                return self._get_power_status_ipmi()
            elif self.gpio_available:
                return self._get_power_status_gpio()
            else:
                return {
                    'status': 'error',
                    'error': 'No power detection available',
                    'power_state': 'unknown'
                }
        except Exception as e:
            logger.error(f"Power status check failed: {e}")
            return {
                'status': 'error',
                'error': str(e),
                'power_state': 'unknown'
            }
    
    # === GPIO Implementation ===
    
    def _power_on_gpio(self):
        """GPIO: Press power button to turn on."""
        return self._press_button_gpio(self.GPIO_POWER_BUTTON, self.POWER_BUTTON_PRESS)
    
    def _power_off_gpio(self, force=False):
        """GPIO: Press power button to shut down (or force off)."""
        duration = self.POWER_BUTTON_HOLD if force else self.POWER_BUTTON_PRESS
        action = "force power off" if force else "graceful shutdown"
        return self._press_button_gpio(self.GPIO_POWER_BUTTON, duration, action=action)
    
    def _power_reset_gpio(self):
        """GPIO: Press reset button."""
        return self._press_button_gpio(self.GPIO_RESET_BUTTON, self.POWER_BUTTON_PRESS, 
                                       action="reset")
    
    def _power_cycle_gpio(self):
        """GPIO: Full power cycle."""
        try:
            # Power off
            self._press_button_gpio(self.GPIO_POWER_BUTTON, self.POWER_BUTTON_HOLD, 
                                   action="cycle_start")
            
            # Wait for shutdown and power discharge
            time.sleep(5)
            
            # Power on
            self._press_button_gpio(self.GPIO_POWER_BUTTON, self.POWER_BUTTON_PRESS, 
                                   action="cycle_end")
            
            logger.info("Power cycle completed")
            return {
                'status': 'ok',
                'message': 'Power cycle completed (off → wait 5s → on)'
            }
        except Exception as e:
            logger.error(f"Power cycle failed: {e}")
            return {'status': 'error', 'error': str(e)}
    
    def _press_button_gpio(self, pin, duration_ms, action="button press"):
        """
        Simulate button press via GPIO pin (pull low for duration).
        
        Args:
            pin: GPIO pin number
            duration_ms: Press duration in milliseconds
            action: Action description for logging
        
        Returns:
            dict with status
        """
        try:
            if self.gpio_lib == 'gpiozero':
                return self._press_button_gpiozero(pin, duration_ms, action)
            elif self.gpio_lib == 'rpi_gpio':
                return self._press_button_rpi_gpio(pin, duration_ms, action)
        except Exception as e:
            logger.error(f"Button press failed: {e}")
            return {'status': 'error', 'error': str(e)}
    
    def _press_button_gpiozero(self, pin, duration_ms, action):
        """GPIO button press using gpiozero."""
        import gpiozero
        
        button = gpiozero.OutputDevice(pin, active_high=False)  # Active low
        button.on()  # Pull low (activate relay)
        time.sleep(duration_ms / 1000.0)
        button.off()  # Release
        button.close()
        
        logger.info(f"GPIO {pin}: {action} ({duration_ms}ms)")
        return {
            'status': 'ok',
            'message': f'Emulated {action}',
            'pin': pin,
            'duration_ms': duration_ms
        }
    
    def _press_button_rpi_gpio(self, pin, duration_ms, action):
        """GPIO button press using RPi.GPIO."""
        import RPi.GPIO as GPIO
        
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)
        GPIO.output(pin, GPIO.LOW)  # Pull low
        time.sleep(duration_ms / 1000.0)
        GPIO.output(pin, GPIO.HIGH)  # Release
        GPIO.cleanup(pin)
        
        logger.info(f"GPIO {pin}: {action} ({duration_ms}ms)")
        return {
            'status': 'ok',
            'message': f'Emulated {action}',
            'pin': pin,
            'duration_ms': duration_ms
        }
    
    def _get_power_status_gpio(self):
        """Read power LED status via GPIO input."""
        try:
            if self.gpio_lib == 'gpiozero':
                import gpiozero
                led = gpiozero.InputDevice(self.GPIO_POWER_LED)
                is_on = led.is_active
                led.close()
            elif self.gpio_lib == 'rpi_gpio':
                import RPi.GPIO as GPIO
                GPIO.setup(self.GPIO_POWER_LED, GPIO.IN)
                is_on = GPIO.input(self.GPIO_POWER_LED)
                GPIO.cleanup(self.GPIO_POWER_LED)
            else:
                return {'status': 'error', 'error': 'GPIO not initialized', 'power_state': 'unknown'}
            
            state = 'on' if is_on else 'off'
            self.power_state = state
            logger.info(f"Power status (GPIO): {state}")
            return {
                'status': 'ok',
                'power_state': state,
                'method': 'GPIO power LED',
                'timestamp': datetime.utcnow().isoformat()
            }
        except Exception as e:
            logger.error(f"Power status GPIO read failed: {e}")
            return {'status': 'error', 'error': str(e), 'power_state': 'unknown'}
    
    # === IPMI Implementation ===
    
    def _power_on_ipmi(self):
        """IPMI: Power on via BMC."""
        try:
            subprocess.run(
                ['ipmitool', '-H', self.ipmi_host, 'power', 'on'],
                check=True, capture_output=True, timeout=10
            )
            logger.info("IPMI power on")
            return {'status': 'ok', 'message': 'Power on (IPMI)'}
        except subprocess.CalledProcessError as e:
            logger.error(f"IPMI power on failed: {e}")
            return {'status': 'error', 'error': f'IPMI failed: {e.stderr.decode()}'}
    
    def _power_off_ipmi(self, force=False):
        """IPMI: Power off via BMC."""
        try:
            cmd = ['ipmitool', '-H', self.ipmi_host, 'power', 'off']
            subprocess.run(cmd, check=True, capture_output=True, timeout=10)
            action = "force off" if force else "power off"
            logger.info(f"IPMI {action}")
            return {'status': 'ok', 'message': f'{action.title()} (IPMI)'}
        except subprocess.CalledProcessError as e:
            logger.error(f"IPMI power off failed: {e}")
            return {'status': 'error', 'error': f'IPMI failed: {e.stderr.decode()}'}
    
    def _power_reset_ipmi(self):
        """IPMI: Reset via BMC."""
        try:
            subprocess.run(
                ['ipmitool', '-H', self.ipmi_host, 'power', 'reset'],
                check=True, capture_output=True, timeout=10
            )
            logger.info("IPMI power reset")
            return {'status': 'ok', 'message': 'Reset (IPMI)'}
        except subprocess.CalledProcessError as e:
            logger.error(f"IPMI power reset failed: {e}")
            return {'status': 'error', 'error': f'IPMI failed: {e.stderr.decode()}'}
    
    def _power_cycle_ipmi(self):
        """IPMI: Power cycle via BMC."""
        try:
            subprocess.run(
                ['ipmitool', '-H', self.ipmi_host, 'power', 'cycle'],
                check=True, capture_output=True, timeout=10
            )
            logger.info("IPMI power cycle")
            return {'status': 'ok', 'message': 'Power cycle (IPMI)'}
        except subprocess.CalledProcessError as e:
            logger.error(f"IPMI power cycle failed: {e}")
            return {'status': 'error', 'error': f'IPMI failed: {e.stderr.decode()}'}
    
    def _get_power_status_ipmi(self):
        """IPMI: Get power status from BMC."""
        try:
            result = subprocess.run(
                ['ipmitool', '-H', self.ipmi_host, 'power', 'status'],
                check=True, capture_output=True, timeout=5
            )
            output = result.stdout.decode().strip().lower()
            
            if 'on' in output:
                state = 'on'
            elif 'off' in output:
                state = 'off'
            else:
                state = 'unknown'
            
            self.power_state = state
            logger.info(f"Power status (IPMI): {state}")
            return {
                'status': 'ok',
                'power_state': state,
                'method': 'IPMI BMC',
                'timestamp': datetime.utcnow().isoformat()
            }
        except Exception as e:
            logger.error(f"IPMI power status failed: {e}")
            return {'status': 'error', 'error': str(e), 'power_state': 'unknown'}
