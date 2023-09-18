#!/usr/bin/env python3
import json
import re
import sqlite3
import os, time
import socket
import logging

import fff_dqmtools
import fff_cluster
import applets.fff_filemonitor as fff_filemonitor

# fff_dqmtools fixed the imports for us
import bottle
import zlib
import itertools
import requests

log = logging.getLogger(__name__)

from ws4py.websocket import WebSocket
from ws4py.client.threadedclient import WebSocketClient


class Database(object):
    def __init__(self, db=None):
        self.db_str = db

        if not self.db_str:
            self.db_str = ":memory:"

        self.listeners = []
        self.conn = sqlite3.connect(self.db_str)

        # create tables if none
        self.create_tables()

    def drop_tables(self):
        cur = self.conn.cursor()
        cur.execute("DROP TABLE IF EXISTS Headers")
        cur.execute("DROP TABLE IF EXISTS Documents")

        self.conn.commit()
        cur.close()

    def create_tables(self):
        cur = self.conn.cursor()

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS Headers (
            id TEXT PRIMARY KEY NOT NULL,
            rev INT NOT NULL,

            timestamp TIMESTAMP,
            hostname TEXT,
            type TEXT,
            tag  TEXT,
            run  INT,
            FOREIGN KEY(id) REFERENCES Documents(id)
        )"""
        )

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS Documents (
            id TEXT PRIMARY KEY NOT NULL,
            rev INT,
            body BLOB
        )"""
        )

        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS M_rev_index ON Headers (rev)")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS M_timestamp_index ON Headers (timestamp)"
        )

        self.conn.commit()
        cur.close()

    def make_header(self, doc, rev=None, write_back=False):
        header = {
            "_id": doc.get("_id"),
            "_rev": doc.get("_rev", rev),
            "timestamp": doc.get("timestamp", doc.get("report_timestamp", time.time())),
            "hostname": doc.get("hostname", None),
            "type": doc.get("type", None),
            "tag": doc.get("tag", None),
            "run": doc.get("run", None),
        }

        if write_back:
            for k, v in header.items():
                doc[k] = v

        return header

    def make_header_from_entry(self, dct):
        header = dict(dct)
        header["_id"] = header["id"]
        header["_rev"] = header["rev"]

        del header["id"]
        del header["rev"]

        return header

    def prepare_docs(self, c, decode=True):
        columns = list(map(lambda x: x[0], c.description))
        body_column = columns.index("body")

        for x in c.fetchall():
            body = x[body_column]

            if decode:
                body = zlib.decompress(body)
                body = json.loads(body)

            yield body

    def prepare_headers(self, c):
        columns = list(map(lambda x: x[0], c.description))

        for x in c.fetchall():
            hit = dict(zip(columns, x))
            hit = self.make_header_from_entry(hit)

            yield hit

    def get_headers(self, reload=False, from_rev=None):
        with self.conn as db:
            c = db.cursor()
            if from_rev is None:
                c.execute(
                    "SELECT id, rev, timestamp, type, hostname, tag, run FROM Headers ORDER BY rev ASC"
                )
            else:
                from_rev = int(from_rev)
                c.execute(
                    "SELECT id, rev, timestamp, type, hostname, tag, run FROM Headers WHERE rev > ? ORDER BY rev ASC",
                    (from_rev,),
                )

            headers = list(self.prepare_headers(c))
            c.close()

        # self.find_first_rev(3600*24*16)

        return headers

    def find_first_rev(self, seconds):
        x = time.time() - seconds
        with self.conn as db:
            c = db.cursor()
            c.execute(
                "SELECT rev, timestamp FROM Headers WHERE timestamp >= ? ORDER BY rev ASC LIMIT 1",
                (x,),
            )
            r = c.fetchone()

            c.close()

    def direct_transactional_upload(self, bodydoc_generator):
        headers = []  # this is used to notify websockets
        with self.conn as db:
            rev = None

            def get_last_rev():
                cur = db.cursor()
                x = cur.execute("SELECT MAX(rev) FROM Headers")
                r = x.fetchone()[0] or 0
                cur.close()
                return r

            for body in bodydoc_generator:
                if rev is None:
                    rev = get_last_rev()

                # get the document
                if isinstance(body, str):
                    doc = json.loads(body)
                else:
                    doc = body

                # not that we ever overflow it ...
                rev = (rev + 1) & ((2**63) - 1)

                # create the header and update the body
                header = self.make_header(doc, rev=rev, write_back=True)
                body = zlib.compress(json.dumps(doc).encode("utf-8"))

                db.execute(
                    "INSERT OR REPLACE INTO Headers (id, rev, timestamp, type, hostname, tag, run) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        header.get("_id"),
                        header.get("_rev"),
                        header.get("timestamp"),
                        header.get("type"),
                        header.get("hostname"),
                        header.get("tag"),
                        header.get("run"),
                    ),
                )

                db.execute(
                    "INSERT OR REPLACE INTO Documents (id, rev, body) VALUES (?, ?, ?)",
                    (
                        header.get("_id"),
                        header.get("_rev"),
                        sqlite3.Binary(body),
                    ),
                )

                headers.append(header)

        self.update_headers(headers)

    def add_listener(self, listener):
        self.listeners.append(listener)

    def remove_listener(self, listener):
        self.listeners.remove(listener)

    def update_headers(self, headers):
        # it is possible that listeners list will change
        # in a different greenlet (ie, exit or something)

        # we don't care, since client should handle
        # the websocket errors.
        if len(headers) == 0:
            return

        copy = list(self.listeners)
        for client in copy:
            client.updateHeaders(headers)


class SyncSocket(WebSocket):
    STATE_NONE = 1
    STATE_INSYNC = 2
    STATE_LISTEN = 3
    STATE_CLOSED = -1

    def opened(self):
        self.backlog = []
        self.state = self.STATE_NONE
        self.close_reason = None

        self.db.add_listener(self)

        log.info("WebSocket connected: %s", self.peer_address)

    def closed(self, code, reason=None, output_log=True):
        self.db.remove_listener(self)

        if output_log:
            log.info(
                "WebSocket disconnected: %s code=%s reason=%s",
                self.peer_address,
                code,
                reason,
            )

    def received_message(self, msg):
        # print "recv:", self, msg, type(msg)
        # sys.stdout.flush()

        jsn = json.loads(msg.data)
        if jsn["event"] == "sync_request":
            known_rev = jsn.get("known_rev", None)
            self.state = self.STATE_INSYNC

            log.info(
                "WebSocket client (%s) requested sync from rev %s",
                self.peer_address,
                known_rev,
            )

            # if know_rev is not zero, we have to send at least a single header
            # to let the web interface to know it is synchronized
            if known_rev is not None:
                known_rev = int(known_rev) - 1

            # send the current state
            # if any changes happen during this time
            # they go into backlog
            self.backlog.insert(0, self.db.get_headers(from_rev=known_rev))
            while len(self.backlog):
                h = self.backlog.pop(0)
                self.sendHeaders(h)

            self.state = self.STATE_LISTEN

        if jsn["event"] == "request_documents":
            ids = set(jsn["ids"])

            with self.db.conn as db:
                c = db.cursor()

                IN = "(" + ",".join("?" * len(ids)) + ")"
                c.execute("SELECT * FROM Documents WHERE id IN " + IN, list(ids))

                docs = list(self.db.prepare_docs(c))
                c.close()

            jsn = json.dumps(
                {
                    "event": "update_documents",
                    "documents": docs,
                }
            ).encode("utf8")

            log.info(
                "WebSocket client (%s) requested %d documents (%d bytes)",
                self.peer_address,
                len(ids),
                len(jsn),
            )
            self.send(jsn, False)

    def sendHeaders(self, headers):
        if not headers:
            return

        # split sending into multiple messages
        # this should be extremely helpful with users on bad connections
        cp = list(headers)

        total_avail = len(cp)
        total_sent = 0

        max_size = 1000
        last_rev = cp[-1]["_rev"]

        while cp:
            to_send, cp = cp[:max_size], cp[max_size:]
            total_sent += len(to_send)

            self.send(
                json.dumps(
                    {
                        "event": "update_headers",
                        "rev": [to_send[0]["_rev"], to_send[-1]["_rev"]],
                        "sync_to_rev": last_rev,
                        "headers": to_send,
                        "total_sent": total_sent,
                        "total_avail": total_avail,
                    }
                ),
                False,
            )

    def updateHeaders(self, headers):
        # this cannot throw
        # or it will kill the input server

        try:
            if self.state == self.STATE_INSYNC:
                self.backlog.append(headers)
            elif self.state == self.STATE_LISTEN:
                self.sendHeaders(headers)
            else:
                pass
        except:
            log.warning("WebSocket error.", exc_info=True)

    @staticmethod
    def proxy_mode(input_messages: list, peer_address: str) -> list:
        # emulate a websocket
        # create it, parse messages and capture everything we send to it
        # we don't need to suppress self.db.add_listener as it has no effect
        # there should be no IO inside here

        # both input and output messages are strings (not json objects)
        output_messages = []

        class Proxy(SyncSocket):
            def __init__(self, peer_address):
                self._peer_address = peer_address

            def send(self, msg, binary=False):
                if binary and isinstance(msg, str):
                    msg = msg.encode("utf-8")
                elif not binary and isinstance(msg, bytes):
                    msg = msg.decode("utf-8")
                output_messages.append(msg)

            @property
            def peer_address(self):
                return self._peer_address

        class ProxyMessage(object):
            def __init__(self, str):
                self.data = str

        c = Proxy(peer_address)
        c.opened()
        for msg in input_messages:
            log.info("Proxy mode insert msg: %s", msg)
            c.received_message(ProxyMessage(msg))
        c.closed(code=1006, reason="Proxy mode end.", output_log=False)

        return output_messages


class WebServer(bottle.Bottle):
    def __init__(self, db=None, opts={}):
        bottle.Bottle.__init__(self)

        self.db = db
        self.opts = opts
        self.secret = opts["web.secret"]
        self.secret_name = opts["web.secret_name"]
        self.setup_routes()

    def setup_routes(self):
        app = self

        static_path = os.path.dirname(__file__)
        static_path = os.path.join(static_path, "../web.static/")

        # from wsgiproxy.app import WSGIProxyApp
        # proxy_app = WSGIProxyApp("https://dqmrubu-c2a06-03-01.cms:9215/sync_proxy")
        # root.mount(proxy_app,"/dqm/dqm-square-origin/redirect/dqmrubu-c2a06-03-01.cms:9215/sync")

        # the decorator to enable cross domain communication
        # for http-proxy stuff
        # from: http://paulscott.co.za/blog/cors-in-python-bottle-framework/
        def enable_cors(fn):
            from bottle import request, response

            def _enable_cors(*args, **kwargs):
                # set CORS headers
                response.headers["Access-Control-Allow-Origin"] = "*"
                response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
                response.headers["Access-Control-Allow-Headers"] = (
                    "Origin, Accept, Content-Type, X-Requested-With, X-CSRF-Token"
                )
                response.headers["Access-Control-Allow-Credentials"] = "true"

                if bottle.request.method != "OPTIONS":
                    # actual request; reply with the actual response
                    return fn(*args, **kwargs)

            return _enable_cors

        def get_cookie(key, raw_cookies):
            """
            Helper method which replaces bottle.request.get_cookie which
            has different behavior for python2 and 3.
            Internally, bottle calls SimpleCookie to parse the cookie string.
            CMSWEB prepends a string before the custom cookies, which breaks
            the parsing.

            A simple example is:

            from http.cookie import SimpleCookie
            SimpleCookie('1234; test=data').values()

            The above worked for python2 but not for 3.
            NB: Bottle is unmaintained since 2014, as of writing.
            """
            cookies = raw_cookies.split(";")
            for cookie in cookies:
                cookie = cookie.strip()
                if "=" not in cookie or key not in cookie:
                    continue
                cookie_key, cookie_value = cookie.split("=")
                if cookie_key == key:
                    return cookie_value
            return None

        def check_secret(secret_value):
            return secret_value == self.secret

        def check_auth(fn):
            def check_auth_(**kwargs):
                host = bottle.request.get_header("host")
                log.info("check_auth(): host=%s", host)
                log.debug(bottle.request.url)
                log.debug(str(bottle.request.auth))
                log.debug(str(bottle.request.remote_route))
                log.debug(str(bottle.request.remote_addr))
                log.debug(str(bottle.request.json))
                log.debug(str(bottle.request.path))
                log.debug(str(bottle.request.cookies.items()))

                if "cmsweb" in bottle.request.url:
                    secret = get_cookie(
                        self.secret_name, bottle.request.headers.raw("Cookie")
                    )
                    if not check_secret(secret):
                        log.info("answer BAD host=%s", host)
                        bottle.redirect("https://cmsweb.cern.ch/")
                    else:
                        return fn(**kwargs)
                else:
                    return fn(**kwargs)

            return check_auth_

        @app.route("/login")
        def login():
            return "<p>Welcome! You are not logged in.</p>"

        @app.route("/static/<filepath:path>")
        @check_auth
        def static(filepath):
            return bottle.static_file(filepath, root=static_path)

        @app.route("/")
        @check_auth
        def index():
            if "cmsweb" in bottle.request.url:
                bottle.redirect("/dqm/dqm-square-origin/static/index.html")
                return
            bottle.redirect("/static/index.html")

        @app.get("/info")
        @check_auth
        def info():
            c = self.db.conn.cursor()
            c.execute("PRAGMA page_size")
            ps = c.fetchone()[0]
            c.execute("PRAGMA page_count")
            pc = c.fetchone()[0]
            c.close()

            return {
                "hostname": fff_cluster.get_host(),
                "timestamp": time.time(),
                "cluster": fff_cluster.get_node(),
                "db_size": ps * pc,
            }

        @app.post("/_upload/")
        # @check_auth
        def upload():
            if "cmsweb" in bottle.request.url:
                return
            from bottle import request

            j = json.loads(request.body.read())
            documents = j["docs"]

            self.db.direct_transactional_upload(documents)
            log.info(
                "Accepted %d document(s) from input connection: %s",
                len(documents),
                request.remote_addr,
            )

        ### @app.route("/get/<id>", method=['GET', 'POST'])
        ### def get_id(id):
        ###     from bottle import request, response
        ###     # check if id known to us
        ###     with self.db.conn as db:
        ###         c = db.cursor()
        ###         c.execute("SELECT * FROM Documents WHERE id = ?", (id, ))
        ###         docs = list(self.db.prepare_docs(c, decode=False))
        ###         c.close()

        ###         if len(docs) == 0:
        ###             raise bottle.HTTPResponse("Doc id not found.", status=404)

        ###         response.content_type = 'application/json'
        ###         if 'deflate' in request.headers.get('Accept-Encoding', []):
        ###             response.add_header("Content-Encoding", "deflate")

        ###             return docs[0]
        ###         else:
        ###             return zlib.decompress(docs[0])

        ### @app.get("/headers/cached/")
        ### def headers():
        ###     from bottle import request, response
        ###     headers = self.db.get_headers()
        ###     body = json.dumps({ 'headers': headers })

        ###     response.content_type = 'application/json'
        ###     if 'deflate' in request.headers.get('Accept-Encoding', []):
        ###         response.add_header("Content-Encoding", "deflate")

        ###         return body.encode("zlib")
        ###     else:
        ###         return body

        @app.route("/utils/kill_proc/<id>", method=["POST"])
        @check_auth
        def kill_proc(id):
            if "cmsweb" in bottle.request.url:
                return
            from bottle import request

            data = json.loads(request.body.read())

            # check if id known to us
            c = self.db.conn.cursor()
            c.execute("SELECT body FROM Documents WHERE id = ?", (id,))
            doc = list(self.db.prepare_docs(c))
            c.close()

            if not doc:
                raise bottle.HTTPResponse("Process not found.", status=404)

            b = doc[0]
            pid = int(b["pid"])

            if pid != int(data["pid"]):
                raise bottle.HTTPResponse("Process and pid not found.", status=404)

            if "exit_code" in b:
                raise bottle.HTTPResponse("Process already died.", status=404)

            signal = int(data["signal"])
            if signal not in [9, 15, 11, 3, 2, 12]:
                raise bottle.HTTPResponse("Invalid signal number.", status=500)

            import subprocess

            r = subprocess.call(["kill", "-s", str(signal), str(pid)])

            body = "Process killed, kill exit_code: %d" % r
            return body

        @app.route("/utils/drop_ids", method=["POST"])
        @check_auth
        def drop_ids():
            if "cmsweb" in bottle.request.url:
                return
            from bottle import request

            data = json.loads(request.body.read())
            ids = data["ids"]

            with self.db.conn as db:
                for id in ids:
                    db.execute("DELETE FROM Headers WHERE id= ?", (id,))
                    db.execute("DELETE FROM Documents WHERE id= ?", (id,))

            self.db.get_headers(reload=True)
            return "Deleted %s rows!" % len(ids)

        def verify_logfile(fn):
            if not fn:
                raise bottle.HTTPResponse(
                    "Log entry not found, invalid esMonitoring.py?", status=500
                )

            fn = os.path.realpath(fn)
            allowed = ["/var/log/hltd/pid/"]

            for p in allowed:
                if os.path.commonprefix([fn, p]) == p:
                    if not os.path.exists(fn):
                        raise bottle.HTTPResponse(
                            "Log file is missing: %s" % fn, status=404
                        )

                    return fn

            raise bottle.HTTPResponse(
                "Log file is in a weird location, access denied.", status=401
            )

        def decode_zlog(fn):
            with open(fn, "rb") as f:
                decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)

                while True:
                    part = os.read(f.fileno(), 1024 * 1024)
                    if len(part) == 0:
                        break

                    yield decoder.decompress(part)

                yield decoder.flush()
                if decoder.unused_data:
                    yield "<!-- gzip stream unfinished, bytes in buffer: %d -->\n" % len(
                        decoder.unused_data
                    )

        def decode_log(fn):
            with open(fn, "rb") as f:
                while True:
                    part = os.read(f.fileno(), 1024 * 1024)
                    if len(part) == 0:
                        break

                    yield part

        @app.route("/utils/show_log/<id>", method=["GET", "POST"])
        @check_auth
        def show_log(id):
            from bottle import response

            c = self.db.conn.cursor()
            c.execute("SELECT body FROM Documents WHERE id = ?", (id,))
            doc = list(self.db.prepare_docs(c))
            c.close()

            b = doc[0]

            startup_fn = b.get("stdout_fn", None)
            startup_iter = []
            if startup_fn:
                startup_iter = decode_log(verify_logfile(startup_fn))

            gzip_fn = b.get("stdlog_gzip", None)
            gzip_iter = []
            if gzip_fn:
                gzip_iter = decode_zlog(verify_logfile(gzip_fn))

            response.add_header("Content-Type", "text/plain; charset=UTF-8")
            chain = itertools.chain(startup_iter, gzip_iter)

            # return "".join(chain)
            return chain

        @app.route("/utils/control_command/<name>/<cmd>", method=["OPTIONS", "POST"])
        @check_auth
        @enable_cors
        def control_command(name, cmd):
            from bottle import response

            group = re.match(r"^(\w+)\.(\w+)$", name)
            if group is None:
                raise bottle.HTTPResponse(
                    "Invalid socket (lock) key, access denied.", status=401
                )

            if group.group(2) not in ["fff_simulator"]:
                raise bottle.HTTPResponse(
                    "Invalid socket (lock) name, access denied.", status=401
                )

            if cmd not in [
                "status",
                "restart",
                "next_run",
                "next_lumi",
                "make_it_crash",
            ]:
                raise bottle.HTTPResponse("Invalid command, access denied.", status=401)

            # sys.stderr.write("Using socket: %s\n" % lkey)
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect("\0" + name)
            sock.sendall(cmd + "\n")
            sock.shutdown(socket.SHUT_WR)

            while True:
                data = sock.recv(4096)
                if len(data) == 0:
                    break

                yield data
            sock.close()

        @app.route("/sync_proxy", method=["OPTIONS", "POST"])
        # @check_auth
        @enable_cors
        def sync_proxy():
            from bottle import request, response

            data = json.loads(request.body.read())
            lst = data.get("messages", [])

            output = SyncSocket.proxy_mode(lst, peer_address=request.remote_addr)
            log.info(str(request.remote_addr))

            response.content_type = "application/json"
            return json.dumps({"messages": output})

        ### API for DQM^2 Mirror
        @app.route("/redirect", method=["OPTIONS", "POST"])
        # @check_auth
        @enable_cors
        def redirect():
            from bottle import request, response

            url = (
                "http://"
                + request.query.path
                + ":"
                + request.query.port
                + "/sync_proxy"
            )
            r = requests.post(
                url, data=request.body, headers=request.headers, timeout=5
            )
            return r.content

        ### API for DQM^2 Control Room
        @app.route("/cr/exe")
        @check_auth
        def cr_api():
            log.info(bottle.request.urlparts)
            log.info(bottle.request.urlparts.query)
            what = bottle.request.query.get("what")

            try:
                if what == "get_dqm_clients":
                    host = bottle.request.query.get("host", default=None)
                    playback = bottle.request.query.get("playback", default=True)
                    cmssw_path = (
                        self.opts["cmssw_path_playback"]
                        if playback == "1"
                        else self.opts["cmssw_path_production"]
                    )
                    cmssw_path += self.opts["dqm_clients_subdir"]
                    clients_path = self.opts["hltd_clients_path"]
                    bottle.response.body = json.dumps(
                        fff_cluster.get_dqm_clients(host, cmssw_path, clients_path)
                    )
                    return bottle.response

                elif what == "change_dqm_client":
                    host = bottle.request.query.get("host", default=None)
                    playback = bottle.request.query.get("playback", default=True)
                    cmssw_path = (
                        self.opts["cmssw_path_playback"]
                        if playback == "1"
                        else self.opts["cmssw_path_production"]
                    )
                    cmssw_path += self.opts["dqm_clients_subdir"]
                    clients_path = self.opts["hltd_clients_path"]
                    client = bottle.request.query.get("client", default=None)
                    state = bottle.request.query.get("state", default=0)
                    bottle.response.body = fff_cluster.change_dqm_client(
                        host, cmssw_path, clients_path, client, state
                    )
                    return bottle.response

                elif what == "get_cmssw_info":
                    answer_1 = fff_cluster.get_cmssw_info(
                        self.opts["cmssw_path_playback"]
                    )
                    answer_2 = fff_cluster.get_cmssw_info(
                        self.opts["cmssw_path_production"]
                    )
                    bottle.response.body = (
                        "\n<strong>Playback:</strong>\n"
                        + answer_1
                        + "\n<strong>Production:</strong>\n"
                        + answer_2
                    )

                    return bottle.response.body

                elif what == "get_dqm_machines":
                    nodes = fff_cluster.get_node()["_all"]
                    if bottle.request.query.get("kind"):
                        type_ = bottle.request.query.get("kind")  # answer
                        for key, lst in nodes.items():
                            if type_ in key:
                                bottle.response.body = json.dumps(lst)
                                return bottle.response
                        bottle.response.body = json.dumps([])
                        return bottle.response
                    bottle.response.body = json.dumps(nodes)
                    return bottle.response

                elif what == "get_hltd_versions":
                    bottle.response.body = json.dumps(
                        fff_cluster.get_rpm_version_all("/opt/hltd")
                    )
                    return bottle.response

                elif what == "get_fff_versions":
                    bottle.response.body = json.dumps(
                        fff_cluster.get_rpm_version_all("/opt/fff_dqmtools")
                    )
                    return bottle.response

                elif what == "get_simulator_config":
                    host = bottle.request.query.get(
                        "host", default="dqmrubu-c2a06-03-01"
                    )
                    bottle.response.body = fff_cluster.get_simulator_config(
                        self.opts, fff_cluster.get_host(), host
                    )
                    return bottle.response

                elif what == "get_simulator_runs":
                    host = bottle.request.query.get(
                        "host", default="dqmrubu-c2a06-03-01"
                    )
                    bottle.response.body = json.dumps(
                        fff_cluster.get_simulator_runs(
                            self.opts, fff_cluster.get_host(), host
                        )
                    )
                    return bottle.response

                elif what == "restart_hltd":
                    host = bottle.request.query.get("host", default=None)
                    bottle.response.body = "Specify host to restart HLTD"
                    bottle.status.status = 400
                    if host:
                        bottle.response.body = fff_cluster.restart_hltd(host)
                        bottle.status.status = 200
                    return bottle.response

                elif what == "restart_fff":
                    host = bottle.request.query.get("host", default=None)
                    bottle.status.body = "Specify host to restart FFF"
                    bottle.status.status = 400
                    if host:
                        bottle.status.body = fff_cluster.restart_fff(host)
                        bottle.response.status = 200
                    return bottle.response

                elif what == "get_hltd_logs":
                    host = bottle.request.query.get("host", default=None)
                    answer = ["Specify host HLTD", "Specify host HLTD"]
                    if host:
                        answer_hltd = fff_cluster.get_txt_file(
                            host, self.opts["hltd_logfile"], 30
                        )
                        answer_anelastic = fff_cluster.get_txt_file(
                            host, self.opts["anelastic_logfile"], 30
                        )

                        answer = [answer_hltd, answer_anelastic]
                    bottle.response.body = json.dumps(answer)
                    return bottle.response

                elif what == "get_fff_logs":
                    host = bottle.request.query.get("host", default=None)
                    answer = "Specify host FFF"
                    if host:
                        answer = fff_cluster.get_txt_file(
                            host, self.opts["logfile"], 30
                        )
                    bottle.response.body = json.dumps([answer])
                    return bottle.response

                elif what == "get_cluster_status":
                    requested_cluster = bottle.request.query.get(
                        "cluster", default="playback"
                    )

                    # Check if any of the clusters matches the cluster name supplied.
                    # clusters' keys are expected to be in the form "<cluster name>_".
                    if not any(
                        [
                            cluster_name.split("_")[0] == requested_cluster
                            for cluster_name in fff_cluster.clusters.keys()
                        ]
                    ):
                        bottle.response.body = json.dumps(
                            f"Cluster {requested_cluster} does not exist. Possible values: {list(map(lambda cluster_name: cluster_name.split('_')[0], fff_cluster.clusters.keys()))}"
                        )
                        bottle.response.status = 404
                        return bottle.response

                    # Find the actual cluster name in the dictionary
                    for cluster_name in fff_cluster.clusters.keys():
                        if cluster_name.split("_")[0] == requested_cluster:
                            requested_cluster = cluster_name
                            break
                    result = {}

                    for host in fff_cluster.clusters[requested_cluster]:
                        # Skip opening a ws to ourselves, gets stuck
                        if fff_cluster.get_host() == host.split(".")[0]:
                            result[host] = {"up": True, "msg": None}
                            continue
                        try:
                            ws = WebSocketClient(
                                f"ws://{host}:9215/sync", protocols=["http-only"]
                            )
                            ws.connect()
                            ws.send(
                                json.dumps({"event": "sync_request", "known_rev": None})
                            )
                            ws.close()
                            result[host] = {"up": True, "msg": None}
                        except Exception as e:
                            log.error(f"Error when opening socket to {host}: {repr(e)}")
                            result[host] = {"up": False, "msg": repr(e)}

                    return json.dumps(result)
                elif what == "start_playback_run":
                    host = bottle.request.query.get(
                        "host", default="dqmrubu-c2a06-03-01"
                    )
                    if fff_cluster.get_host() != host:
                        url = (
                            "http://"
                            + host
                            + ":"
                            + str(self.opts["web.port"])
                            + "/cr/exe?"
                            + bottle.request.urlparts.query
                        )
                        r = requests.get(
                            url,
                            data=bottle.request.body,
                            headers=bottle.request.headers,
                            timeout=60,
                        )
                        return r.content

                    run_number = bottle.request.query.get("run_number", default=None)
                    run_class = bottle.request.query.get("run_key", default=None)
                    number_of_ls = bottle.request.query.get("number_of_ls", default=0)

                    cfg = fff_cluster.get_simulator_config(
                        self.opts, fff_cluster.get_host(), host
                    )
                    cfg = json.loads(cfg)

                    run_path = os.path.dirname(cfg["source"])
                    cfg = fff_cluster.update_config(
                        cfg, "source", run_path + "/" + run_number
                    )
                    cfg = fff_cluster.update_config(cfg, "run_key", run_class)
                    cfg = fff_cluster.update_config(
                        cfg, "number_of_ls", int(number_of_ls)
                    )
                    fff_cluster.write_config(self.opts, cfg)

                    # start new run
                    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    sock.connect("\0" + fff_dqmtools.get_lock_key("fff_simulator"))
                    sock.sendall("next_run\n")
                    sock.shutdown(socket.SHUT_WR)
                    sock.close()
                    return "start_playback_run Ok"

            except Exception as error_log:
                msg = f"cr_api@{what}: error: {error_log}"
                log.warning(msg)
                return msg, 400
            log.warning(f"cr_api@{what} : No actions defined for that request")
            return f"No actions defined for request {what}", 400


def run_web_greenlet(db, host="0.0.0.0", port=9215, opts={}, **kwargs):
    listener = (
        host,
        port,
    )

    from ws4py.server.geventserver import WSGIServer
    from ws4py.server.wsgiutils import WebSocketWSGIApplication

    SyncSocket.db = db

    static_app = WebServer(db, opts)
    static_app.mount("/sync", WebSocketWSGIApplication(handler_cls=SyncSocket))

    server = WSGIServer(listener, static_app)

    log.info("Using db: %s." % (db.db_str))
    log.info("Started web server at [%s]:%d" % (host, port))
    log.info("Go to http://%s:%d/" % (fff_cluster.get_host(), port))

    server.serve_forever()


import gevent


@fff_dqmtools.fork_wrapper(__name__, uid="dqmpro", gid="dqmpro")
@fff_dqmtools.lock_wrapper
def __run__(opts, **kwargs):
    global log
    log = kwargs["logger"]

    db_string = opts["web.db"]
    port = opts["web.port"]

    db = Database(db=db_string)

    fwt = gevent.spawn(run_web_greenlet, db, port=port, opts=opts)
    gevent.joinall([fwt], raise_error=True)
