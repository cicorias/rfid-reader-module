"""
    The rfidreader module is utilizing the evdev system to pull event data from the input device.
    This is preferable over an approach using getch() since it can lock to a particular input device.
    The input device mount path must be forwarded through the docker container in order to access the device.
    On a new device event the module first checks if it's a KEY_DOWN, then append the char to the buffer.
    When the callback scans the terminating character it will call _parse_id which validates,
    updates the variable passed through get_current_id(), and clears the buffer on exit.
    Validation can use a regex, but simply relying on length for now.

    TODO
    X persistence (implement shelf from session.py)
    X update requirements.txt
    X @NI make sense to move init to _start in thread?
        then when the thread is stopped it'll try to restart itself
        on a get_current_id
        (fixed by infinite check in thread)
    X verify actual events from hardware playback
    X @NI self._lock here for get_current_id?
"""

import time
import threading
from datetime import datetime
from logging import Logger
import evdev
import shelve

# need a keymap to resolve the ascii equivalent of incoming scancodes
# doubles as a way to validate characters against a desired set containing
# only the characters that the rfid reader can produce
SCANCODE_MAP = {
    0: None, 28: 'KEY_ENTER',
    2: u'1', 3: u'2', 4: u'3', 5: u'4', 6: u'5', 7: u'6', 8: u'7', 9: u'8', 10: u'9', 11: u'0',
    16: u'Q', 17: u'W', 18: u'E', 19: u'R', 20: u'T', 21: u'Y', 22: u'U', 23: u'I', 24: u'O',
    25: u'P', 30: u'A', 31: u'S', 32: u'D', 33: u'F', 34: u'G', 35: u'H', 36: u'J', 37: u'K',
    38: u'L', 44: u'Z', 45: u'X', 46: u'C', 47: u'V', 48: u'B', 49: u'N', 50: u'M',
}

KEY_DOWN = 1
DEVICE_PATH = ''
ID_TIMEOUT_SECONDS = 8
ID_LENGTH = 12
ID_TERMINATOR = SCANCODE_MAP[28]
HARDWARE_TIMEOUT = 5
SHELVE_PATH = '/tmp/rfid'


class RFIDReader:
    def __init__(
            self,
            device_path: str,
            logger: Logger,
            id_length: str = ID_LENGTH,
            id_terminator: str = ID_TERMINATOR,
            id_timeout_seconds: int = ID_TIMEOUT_SECONDS,
            hardware_timeout: float = HARDWARE_TIMEOUT,
    ):

        self.logger = logger
        self.database = shelve.open(SHELVE_PATH)
        self.current_id = self.database.get('current_id')
        self.device_path = device_path
        self.id_length = id_length
        self.id_terminator = id_terminator
        self.id_timeout_seconds = id_timeout_seconds
        self.hardware_timeout = hardware_timeout
        self.id_buffer = ''
        self.id_timestamp = self.database.get('id_timestamp') 
        self.thread = threading.Thread(target=self._start, name="RFIDReader")
        self.thread.daemon = True
        self.thread.start()

    """
    Thread method to handle blocking evdev read_loop() call
    """
    def _start(self):
        while True:
            try:
                self.logger.info('initializing rfid device: %s', self.device_path)
                self.device = evdev.InputDevice(self.device_path)
                self.device.grab()      # get exclusive access to reader to avoid keystrokes to OS
                self.id_buffer = ''
                self.logger.info('rfid device found!')
                for event in self.device.read_loop():
                    data = evdev.categorize(event)

                    if event.type == evdev.ecodes.EV_KEY:
                        if data.keystate == KEY_DOWN:
                            try:
                                key = SCANCODE_MAP[data.scancode]
                            except KeyError:
                                pass
                        else:
                            if(key == self.id_terminator):      # check for terminating char
                                self._parse_id()
                            else:
                                self.id_buffer += key           # append the ascii value
            except KeyboardInterrupt:
                quit()

            except Exception as e:
                self.logger.error('rfid device fault: %s', e)
                self.logger.info('retrying in %s seconds... (HARDWARE_TIMEOUT)', self.hardware_timeout)
                time.sleep(self.hardware_timeout)

    """
    Internal method to validate and clear the contents of the InputEvent character buffer.
    ID format validation logic uses an integer representing the length, pattern is
    set at the module level but can easily be broken out to a twin property if needed.
    """
    def _parse_id(self):
        if len(self.id_buffer) == self.id_length:
            self.current_id = self.id_buffer
            self.database['current_id'] = self.current_id
            self.id_timestamp = datetime.utcnow()
            self.database['id_timestamp'] = self.id_timestamp
            self.logger.debug('new id set: [%s]', self.current_id)
        else:
            self.current_id = None
            self.logger.error('malformed id: %s', self.id_buffer)
        self.id_buffer = ''

    """
    Returns current validated ID.
    The method currently timestamps the query to support timeout due to inactivity.
    """
    def get_current_id(self):
        if self.current_id is not None:
            if (datetime.utcnow() - self.id_timestamp).total_seconds() > self.id_timeout_seconds:
                self.logger.info('rfid session expired: [%s] (%ssec)', self.current_id, self.id_timeout_seconds)
                self.current_id = None
                self.id_timestamp = None
                self.database['current_id'] = None
                self.database['id_timestamp'] = None

        return self.current_id
