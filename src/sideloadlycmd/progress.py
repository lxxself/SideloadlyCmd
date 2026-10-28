"""Safe stage events and bounded wait notifications; no subprocess output or secrets."""
from contextvars import ContextVar
import threading
import time

_current = ContextVar('sideloadly_progress', default=None)


def stage(name, message, **counts):
    progress = _current.get()
    if progress is not None:
        progress.stage(name, message, **counts)


class Progress:
    def __init__(self, emit, command, interval=10):
        self.emit = emit
        self.command = command
        self.interval = interval
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.started = time.monotonic()
        self.name = 'starting'
        self.message = 'Starting command'
        self.token = None
        self.thread = None

    def start(self):
        self.token = _current.set(self)
        self.stage('starting', 'Running ' + self.command)
        self.thread = threading.Thread(target=self._wait, name='sideloadly-progress', daemon=True)
        self.thread.start()

    def stage(self, name, message, **counts):
        with self.lock:
            self.name, self.message = name, message
            self.emit('command_progress', command=self.command, stage=name, message=message, **counts)

    def _wait(self):
        while not self.stop.wait(self.interval):
            with self.lock:
                self.emit('command_waiting', command=self.command, stage=self.name,
                          message='Still waiting: ' + self.message,
                          elapsed_seconds=round(time.monotonic() - self.started, 1))

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join()
        if self.token is not None:
            _current.reset(self.token)
