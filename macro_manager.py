import json
import time
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


# Default macros that ship with every installation
DEFAULT_MACROS = [
    {
        'id': 'ctrl_alt_del',
        'name': 'Ctrl+Alt+Del',
        'description': 'Send Ctrl+Alt+Delete',
        'steps': [
            {'type': 'ctrl_alt_del'},
        ],
        'builtin': True,
    },
    {
        'id': 'enter_bios_del',
        'name': 'Enter BIOS (DEL)',
        'description': 'Rapidly press DEL to enter BIOS setup',
        'steps': [
            {'type': 'key', 'keycode': 0x4C, 'repeat': 10, 'interval': 0.15},
        ],
        'builtin': True,
    },
    {
        'id': 'enter_bios_f2',
        'name': 'Enter BIOS (F2)',
        'description': 'Rapidly press F2 to enter BIOS setup',
        'steps': [
            {'type': 'key', 'keycode': 0x3B, 'repeat': 10, 'interval': 0.15},
        ],
        'builtin': True,
    },
    {
        'id': 'enter_bios_f12',
        'name': 'Boot Menu (F12)',
        'description': 'Rapidly press F12 for boot menu',
        'steps': [
            {'type': 'key', 'keycode': 0x45, 'repeat': 10, 'interval': 0.15},
        ],
        'builtin': True,
    },
    {
        'id': 'win_run',
        'name': 'Windows Run Dialog',
        'description': 'Open Windows Run dialog (Win+R)',
        'steps': [
            {'type': 'key_mod', 'keycode': 0x15, 'modifiers': 0x08},
            {'type': 'delay', 'ms': 100},
        ],
        'builtin': True,
    },
    {
        'id': 'win_lock',
        'name': 'Lock Workstation',
        'description': 'Lock Windows workstation (Win+L)',
        'steps': [
            {'type': 'key_mod', 'keycode': 0x0F, 'modifiers': 0x08},
        ],
        'builtin': True,
    },
    {
        'id': 'linux_terminal',
        'name': 'Open Terminal (Linux)',
        'description': 'Open terminal on most Linux DEs (Ctrl+Alt+T)',
        'steps': [
            {'type': 'key_mod', 'keycode': 0x17, 'modifiers': 0x05},
        ],
        'builtin': True,
    },
    {
        'id': 'alt_tab',
        'name': 'Alt+Tab',
        'description': 'Switch windows',
        'steps': [
            {'type': 'key_mod', 'keycode': 0x2B, 'modifiers': 0x04},
        ],
        'builtin': True,
    },
]


class MacroManager:
    """
    Manages keyboard macros — named sequences of HID keystrokes that can
    be saved, loaded, and replayed on demand.

    Macros are stored in a JSON file alongside other config files.
    Each macro has:
        - id: unique string identifier
        - name: display name
        - description: optional description
        - steps: list of step dicts (type, keycode, modifiers, delay, repeat, text)
        - builtin: True for default macros (cannot be deleted)

    Step types:
        - key: press+release a single key       {type:'key', keycode:int, repeat?:int, interval?:float}
        - key_mod: key with modifiers             {type:'key_mod', keycode:int, modifiers:int}
        - text: type a string                     {type:'text', text:str}
        - delay: pause between steps              {type:'delay', ms:int}
        - ctrl_alt_del: special combo             {type:'ctrl_alt_del'}
    """

    def __init__(self, macros_path='./macros.json'):
        self._path = Path(macros_path)
        self._macros = []
        self._lock = threading.Lock()
        self._running = False
        self._abort = False
        self.load()

    def load(self):
        """Load macros from disk, merging with defaults."""
        with self._lock:
            saved = []
            if self._path.exists():
                try:
                    saved = json.loads(self._path.read_text())
                except Exception as e:
                    logger.error(f"Failed to load macros: {e}")

            # Build dict of saved macros by id
            saved_map = {m['id']: m for m in saved if 'id' in m}

            # Merge: defaults first, then user macros
            merged = []
            seen_ids = set()
            for d in DEFAULT_MACROS:
                if d['id'] in saved_map:
                    # Preserve user edits to builtin macros
                    m = saved_map[d['id']]
                    m['builtin'] = True
                    merged.append(m)
                else:
                    merged.append(dict(d))
                seen_ids.add(d['id'])

            for m in saved:
                if m.get('id') and m['id'] not in seen_ids:
                    m['builtin'] = False
                    merged.append(m)
                    seen_ids.add(m['id'])

            self._macros = merged

    def save(self):
        """Persist macros to disk."""
        with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._path.write_text(json.dumps(self._macros, indent=2))
            except Exception as e:
                logger.error(f"Failed to save macros: {e}")

    def list_macros(self):
        """Return all macros (metadata only, no step details for list view)."""
        with self._lock:
            return [
                {
                    'id': m['id'],
                    'name': m.get('name', m['id']),
                    'description': m.get('description', ''),
                    'builtin': m.get('builtin', False),
                    'step_count': len(m.get('steps', [])),
                }
                for m in self._macros
            ]

    def get_macro(self, macro_id):
        """Return a full macro by ID, or None."""
        with self._lock:
            for m in self._macros:
                if m['id'] == macro_id:
                    return dict(m)
        return None

    def create_macro(self, macro_data):
        """
        Create or update a macro.

        Args:
            macro_data: dict with id, name, steps, etc.

        Returns:
            The saved macro dict, or error string.
        """
        mid = macro_data.get('id', '').strip()
        if not mid:
            return 'Macro ID is required'
        if not macro_data.get('name', '').strip():
            return 'Macro name is required'
        steps = macro_data.get('steps', [])
        if not isinstance(steps, list):
            return 'Steps must be a list'

        # Validate steps
        for i, step in enumerate(steps):
            if not isinstance(step, dict) or 'type' not in step:
                return f'Step {i} is invalid'
            st = step['type']
            if st not in ('key', 'key_mod', 'text', 'delay', 'ctrl_alt_del'):
                return f'Step {i} has unknown type: {st}'

        with self._lock:
            # Check if updating existing
            for j, m in enumerate(self._macros):
                if m['id'] == mid:
                    self._macros[j] = {
                        'id': mid,
                        'name': macro_data['name'].strip(),
                        'description': macro_data.get('description', ''),
                        'steps': steps,
                        'builtin': m.get('builtin', False),
                    }
                    self.save()
                    return self._macros[j]

            # New macro
            new_macro = {
                'id': mid,
                'name': macro_data['name'].strip(),
                'description': macro_data.get('description', ''),
                'steps': steps,
                'builtin': False,
            }
            self._macros.append(new_macro)

        self.save()
        return new_macro

    def delete_macro(self, macro_id):
        """Delete a user-created macro. Returns True/False."""
        with self._lock:
            for i, m in enumerate(self._macros):
                if m['id'] == macro_id:
                    if m.get('builtin'):
                        return False
                    self._macros.pop(i)
                    self.save()
                    return True
        return False

    def execute_macro(self, macro_id, hid_controller):
        """
        Execute a macro by sending its steps to the HID controller.

        Args:
            macro_id: ID of the macro to execute.
            hid_controller: CH9329HIDController instance.

        Returns:
            True on success, error string on failure.
        """
        macro = self.get_macro(macro_id)
        if not macro:
            return 'Macro not found'
        if not hid_controller or not hid_controller.connected:
            return 'HID device not available'

        if self._running:
            return 'Another macro is already running'

        self._running = True
        self._abort = False

        try:
            for step in macro.get('steps', []):
                if self._abort:
                    break
                self._execute_step(step, hid_controller)
        except Exception as e:
            logger.error(f"Macro execution error: {e}")
            return str(e)
        finally:
            self._running = False
            self._abort = False

        return True

    def abort_macro(self):
        """Abort a currently running macro."""
        self._abort = True

    @property
    def is_running(self):
        return self._running

    def _execute_step(self, step, hid):
        """Execute a single macro step."""
        st = step['type']

        if st == 'ctrl_alt_del':
            hid.send_ctrl_alt_del()

        elif st == 'key':
            keycode = int(step.get('keycode', 0))
            repeat = int(step.get('repeat', 1))
            interval = float(step.get('interval', 0.05))
            for _ in range(repeat):
                if self._abort:
                    break
                hid.send_key(keycode, True)
                time.sleep(0.02)
                hid.send_key(keycode, False)
                if repeat > 1:
                    time.sleep(interval)

        elif st == 'key_mod':
            keycode = int(step.get('keycode', 0))
            modifiers = int(step.get('modifiers', 0))
            hid.send_key_with_modifier(keycode, modifiers)

        elif st == 'text':
            text = step.get('text', '')
            if text:
                hid.send_text(text)

        elif st == 'delay':
            ms = int(step.get('ms', 100))
            time.sleep(ms / 1000.0)
