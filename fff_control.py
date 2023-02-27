#!/usr/bin/env python3

# Provides control socket for fff_dqmtools' applets.
# This socket is already used for locking, this just listens to it.

# Applet must not block (ie use gevent sleeps and gevent selects)
# Connectable via socat, eg. socat - ABSTRACT-CONNECT:bd68c7bb.fff_filemonitor

import fff_dqmtools
import gevent
import struct

class Ctrl(object):
    def __init__(self, log, sock, lkey):
        self.log = log
        self.sock = sock
        self.lkey = lkey

    def handle_line(self, line, write_f):
        # override me
        line = line.strip()

        if line == "status":
            write_f("ok\n")
        else:
            write_f("r: %s\n" % line)

    def handle_conn(self, cli_sock):
        # this function runs in separate greenlet
        f = None
        try:
            self.log.info("Accepted control connection: %s", cli_sock)

            f = cli_sock.makefile("rw")
            def write_f(data):
                f.write(data) # MAYBE CHANGE TO f.write( bytes(data, 'utf-8') ) ?
                f.flush()

            while True:
                l = f.readline()
                if not l:
                    break

                self.handle_line(l, write_f)

            self.log.info("Closed control connection: %s", cli_sock)
        finally:
            if f:
                f.close()
            cli_sock.close()

    def run_greenlet(self):
        self.sock.listen(15)
        self.log.info("Control socket open: %s / %s", self.sock, self.lkey)

        while True:
            cli, addr = self.sock.accept()
            gevent.spawn(self.handle_conn, cli)

    @classmethod
    def enable(cls, log, lkey, sock):
        ctrl = cls(log, sock, lkey)

        control_t = gevent.spawn(ctrl.run_greenlet)
        return control_t, ctrl


if __name__ == "__main__":
    import sys
    from fff_dqmtools import get_lock_key
    from gevent import socket

    if len(sys.argv) != 3:
        sys.stderr.write("Usage: %s applet_name command\n")
        sys.exit(-1)

    lkey = get_lock_key(sys.argv[1])
    cmd = sys.argv[2]

    #sys.stderr.write("Using socket: %s\n" % lkey)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect("\0" + lkey)

    sock.sendall( bytes(cmd + "\n", 'utf-8') )
    sock.shutdown(socket.SHUT_WR)

    while True:
        data = sock.recv(4096)
        if len(data) == 0:
            break

        sys.stdout.write(data.decode('utf-8'))
        sys.stdout.flush()

    sock.close()
