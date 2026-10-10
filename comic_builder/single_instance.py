"""Collect Explorer launches in one per-user Qt application."""
import hashlib
import json
import tempfile
import time
from pathlib import Path
from PySide6.QtCore import QObject, Signal, QLockFile, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class LaunchBroker(QObject):
    paths_received = Signal(list)

    def __init__(self, name=None, parent=None):
        super().__init__(parent)
        self.name = name or 'ComicStitcher-' + hashlib.sha256(str(Path.home()).encode()).hexdigest()[:20]
        self.lock = QLockFile(str(Path(tempfile.gettempdir()) / (self.name + '.lock')))
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._accept)
        self.sockets = {}
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(900)
        self.timer.timeout.connect(self._flush)
        self.pending = []

    def start(self, paths):
        if not self.lock.tryLock(0):
            socket = QLocalSocket()
            deadline = time.monotonic() + 5
            while True:
                socket.connectToServer(self.name)
                if socket.waitForConnected(250):
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError('The running Comic Stitcher window could not be contacted. Please try again.')
                socket.abort()
                time.sleep(.05)
            socket.write((json.dumps([str(p) for p in paths]) + '\n').encode())
            if not socket.waitForBytesWritten(5000):
                raise RuntimeError('Could not send this selection to Comic Stitcher.')
            socket.disconnectFromServer()
            return False
        QLocalServer.removeServer(self.name)
        if not self.server.listen(self.name):
            self.lock.unlock()
            raise RuntimeError(self.server.errorString())
        if paths:
            self.submit(paths)
        return True

    def submit(self, paths):
        self.pending.extend(paths)
        self.timer.start()

    def _accept(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            self.sockets[socket] = bytearray()
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(lambda s=socket: self._discard(s))
            self._read(socket)

    def _read(self, socket):
        data = self.sockets.get(socket)
        if data is None:
            return
        data.extend(bytes(socket.readAll()))
        if len(data) > 1024 * 1024:
            socket.abort()
            return
        if b'\n' in data:
            try:
                paths = json.loads(bytes(data).split(b'\n', 1)[0])
                if isinstance(paths, list) and all(isinstance(p, str) for p in paths):
                    self.submit([Path(p) for p in paths])
            except (ValueError, UnicodeError):
                pass
            data.clear()

    def _discard(self, socket):
        self._read(socket)
        self.sockets.pop(socket, None)
        socket.deleteLater()

    def _flush(self):
        paths, self.pending = self.pending, []
        self.paths_received.emit(paths)

    def close(self):
        self.server.close()
        self.lock.unlock()
