import json
import sqlite3
import os, sys, time
import socket
import logging

cd = os.path.dirname(__file__)
sys.path.append(os.path.join(cd, "../"))

import applets.fff_filemonitor as fff_filemonitor

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: %s <output_directory> <database_file>" % sys.argv[0])
        print("or")
        print("Usage: %s upload <database_file>" % sys.argv[0])
        sys.exit(1)

    path = sys.argv[1]

    def write_files(lst):
        for n_id, n_body in lst:
            fp = os.path.join(path, n_id + ".jsn")
            print("Creating file %s size=%d" % (fp, len(n_body)))
            fff_filemonitor.atomic_create_write(fp, n_body)

    def upload_files(lst):
        bodies = map(lambda x: x[1], lst)
        print("Uploading %d documents." % (len(bodies),))
        fff_filemonitor.socket_upload(bodies)

    def upload(lst):
        if path == "upload":
            upload_files(upload_buffer)
        else:
            write_files(upload_buffer)

    upload_buffer = []
    for db in sys.argv[2:]:
        print("Opening db:", db)

        conn = sqlite3.connect(db)
        c = conn.cursor()
        c.execute("SELECT id, body FROM Monitoring")

        for x in iter(c.fetchone, None):
            n_id = x[0]
            n_body = x[1]
            upload_buffer.append(
                (
                    n_id,
                    n_body,
                )
            )

            if len(upload_buffer) >= 1000:
                upload(upload_buffer)
                upload_buffer = []

        c.close()

    upload(upload_buffer)
    upload_buffer = []
