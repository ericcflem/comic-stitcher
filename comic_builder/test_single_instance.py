import time
import socket as native_socket
import unittest
from pathlib import Path
from uuid import uuid4
from PySide6.QtCore import QCoreApplication
from PySide6.QtNetwork import QLocalSocket
from .single_instance import LaunchBroker


class BrokerTests(unittest.TestCase):
    def test_local_socket_burst_collected_in_one_batch(self):
        try:
            probe = native_socket.socket(native_socket.AF_UNIX) if hasattr(native_socket, 'AF_UNIX') else None
            if probe is not None:
                probe.close()
        except PermissionError:
            self.skipTest('This execution environment blocks local sockets')
        app = QCoreApplication.instance() or QCoreApplication([])
        broker = LaunchBroker('cs-test-' + uuid4().hex[:8])
        received = []
        broker.paths_received.connect(received.append)
        try:
            # The test connects every client before it lets Qt process anything, which a
            # running app never does. Hold the batching window open meanwhile, so a slow
            # machine's connection time is not mistaken for the end of the burst.
            interval = broker.timer.interval()
            broker.timer.setInterval(60_000)
            self.assertTrue(broker.start([Path('first.cbz')]))
            sockets = []
            for n in range(12):
                socket = QLocalSocket()
                socket.connectToServer(broker.name)
                self.assertTrue(socket.waitForConnected(1000))
                socket.write(('["book%d.cbz"]\n' % n).encode())
                socket.waitForBytesWritten(1000)
                sockets.append(socket)
            broker.timer.setInterval(interval)
            broker.timer.start()
            deadline = time.monotonic() + 3
            while not received and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.005)
            self.assertEqual(len(received), 1)
            self.assertEqual(len(received[0]), 13)
            self.assertEqual(received[0][0], Path('first.cbz'))
            for socket in sockets:
                socket.abort()
        finally:
            broker.close()

    def test_burst_debounce_preserves_all_paths(self):
        app = QCoreApplication.instance() or QCoreApplication([])
        broker = LaunchBroker('cs-debounce-test')
        received = []
        broker.paths_received.connect(received.append)
        broker.timer.setInterval(20)
        for n in range(12):
            broker.submit([Path('book%d.cbz' % n)])
        deadline = time.monotonic() + 1
        while not received and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.005)
        self.assertEqual(received, [[Path('book%d.cbz' % n) for n in range(12)]])
        broker.close()
