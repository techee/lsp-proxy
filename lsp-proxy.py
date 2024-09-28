#!/usr/bin/env python3
#
# Author:  Jiri Techet, 2024
# License: GPL v2 or later

from abc import ABC, abstractmethod
import argparse
import asyncio
import json
import signal
import sys


preserved_common_requests = [
    'initialize', 'shutdown',
    'window/showMessageRequest', 'window/showDocument',
    'workspace/workspaceFolders', 'workspace/applyEdit'
]
preserved_client_server_notifications = [
    'initialized', 'exit',
    'textDocument/didOpen', 'textDocument/didChange', 'textDocument/didSave', 'textDocument/didClose',
    'workspace/didChangeWorkspaceFolders', 'workspace/didChangeConfiguration'
]
preserved_server_client_notifications = [
    'textDocument/publishDiagnostics',
    'window/showMessage', 'window/logMessage'
]


def log(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


async def read_message(srv, stream):
    try:
        # HTTP-like header separated by newline
        header = await stream.readuntil(b'\r\n\r\n')
    except asyncio.exceptions.IncompleteReadError:
        if not srv or (not srv.shutdown_received and srv.is_connected() and not srv.get_stream_reader().at_eof()):
            log('Invalid HTTP message, separator between header and body not found')
        return None

    # without the 2 trailing empty strings because of '\r\n\r\n'
    lines = header.split(b'\r\n')[:-2]
    length = 0
    for line in lines:
        key, val = line.split(b':', 2)
        key = key.lower()
        if key == b'content-length':
            length = int(val.strip())

    body = b''
    if length > 0:
        try:
            body = await stream.readexactly(length)
        except asyncio.exceptions.IncompleteReadError:
            log(f'Invalid HTTP message, body shorter than Content-Length: {length}')
            return None

    try:
        return json.loads(body)
    except ValueError:
        log('Invalid JSON in message body')
        return None


class Server(ABC):
    def __init__(self, is_primary):
        self.pending_client_server_requests = {}
        self.pending_server_client_requests = {}
        self.is_primary = is_primary
        self.initialize_msg = None
        self.shutdown_received = False
        self.diagnostics = {}
        self.initialization_options = None
        self.use_diagnostics = True

    def reset_task(self):
        self.task = asyncio.create_task(read_message(self, self.get_stream_reader()))

    @abstractmethod
    async def connect(self) -> bool:
        return False

    @abstractmethod
    def disconnect(self):
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        return False

    @abstractmethod
    async def wait_for_completion(self):
        pass

    @abstractmethod
    def get_stream_reader(self) -> asyncio.StreamReader:
        return None

    @abstractmethod
    def get_stream_writer(self) -> asyncio.StreamWriter:
        return None

    @abstractmethod
    def get_name(self) -> str:
        return ""


class StdioServer(Server):
    def __init__(self, cmd, args, primary):
        super().__init__(primary)
        self._cmd = cmd
        self._args = args
        self._proc = None

    async def connect(self):
        try:
            self._proc = await asyncio.create_subprocess_exec(self._cmd, *self._args,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
            return True
        except FileNotFoundError as err:
            log(err)
        return False

    def is_connected(self):
        assert self._proc
        return self._proc.returncode is None

    def disconnect(self):
        if self._proc and self.is_connected():
            self._proc.terminate()

    async def wait_for_completion(self):
        assert self._proc
        await self._proc.wait()

    def get_stream_reader(self):
        assert self._proc and self._proc.stdout
        return self._proc.stdout

    def get_stream_writer(self):
        assert self._proc and self._proc.stdin
        return self._proc.stdin

    def get_name(self):
        return self._cmd


class SocketServer(Server):
    def __init__(self, host, port, primary):
        super().__init__(primary)
        self._host = host
        self._port = port
        self._reader = None
        self._writer = None

    async def connect(self):
        try:
            self._reader, self._writer = await asyncio.open_connection(self._host, self._port)
            return True
        except ConnectionRefusedError as err:
            log(err)
        return False

    def is_connected(self):
        assert self._writer
        return not self._writer.is_closing()

    def disconnect(self):
        if self._writer and self.is_connected():
            self._writer.close()

    async def wait_for_completion(self):
        assert self._writer
        self._writer.close()
        await self._writer.wait_closed()

    def get_stream_reader(self):
        assert self._reader
        return self._reader

    def get_stream_writer(self):
        assert self._writer
        return self._writer

    def get_name(self):
        return f'{self._host}:{self._port}'


class Proxy:
    def __init__(self, servers):
        self.servers = servers
        self.initialize_id = -1
        self.shutdown_id = -1

    def all_initialized(self):
        return all([srv.initialize_msg for srv in self.servers])

    def all_shutdown(self):
        return all([srv.shutdown_received for srv in self.servers])

    def get_primary(self):
        return next((srv for srv in self.servers if srv.is_primary), self.servers[0])

    def get_merged_diagnostics(self, uri):
        diags = []
        for srv in self.servers:
            if srv.use_diagnostics and uri in srv.diagnostics:
                diags += srv.diagnostics[uri]
        return diags

    def construct_message(self, msg):
        msg_str = json.dumps(msg).encode('utf-8')
        return b'Content-Length: ' + str(len(msg_str)).encode('utf-8') + b'\r\n\r\n' + msg_str

    def filter_msg(self, method, is_primary, preserved_methods):
        if is_primary:
            return False

        return method not in preserved_methods

    async def process(self, srv, writer, msg, from_server, preserved_methods):
        method = msg['method'] if 'method' in msg else None
        iden = msg['id'] if 'id' in msg else None

        should_send = False

        if from_server:
            pending = srv.pending_client_server_requests
        else:
            pending = srv.pending_server_client_requests

        if iden in pending:
            # this is a response to request whose id we already have in pending so
            # this should be sent
            should_send = True
            # update method name based on what we previously assigned to the id
            method = pending[iden]
            del pending[iden]
        elif not self.filter_msg(method, srv.is_primary, preserved_methods):
            # this is a request or notification that hasn't been filtered and should
            # be sent
            should_send = True
            if method and iden:
                if from_server:
                    pending = srv.pending_server_client_requests
                else:
                    pending = srv.pending_client_server_requests
                # store request id's into pending so we send them when responses arrive
                pending[iden] = method

        if from_server:
            if iden == self.initialize_id:
                srv.initialize_msg = msg
                should_send = self.all_initialized()
                # send initialize response only when all servers returned response
                if should_send:
                    # send the primary server's initialize response
                    msg = self.get_primary().initialize_msg
            elif iden == self.shutdown_id:
                srv.shutdown_received = True
                # send shutdown response only when all servers returned response
                should_send = self.all_shutdown()
        else:
            if method == 'initialize':
                self.initialize_id = iden
                if srv.initialization_options:
                    msg['params']['initializationOptions'] = srv.initialization_options
                elif not srv.is_primary:
                    msg['params']['initializationOptions'] = None
            elif method == 'workspace/didChangeConfiguration':
                if srv.initialization_options:
                    msg['params']['settings'] = srv.initialization_options
                elif not srv.is_primary:
                    msg['params']['settings'] = None
            elif method == 'shutdown':
                self.shutdown_id = iden

        if should_send:
            method_str = method if method else "no method"
            if from_server:
                if method == 'textDocument/publishDiagnostics':
                    uri = msg['params']['uri']
                    srv.diagnostics[uri] = msg['params']['diagnostics']
                    # modify msg to contain diagnostics from all servers
                    msg['params']['diagnostics'] = self.get_merged_diagnostics(uri)
                log(f'    C <-- S {method_str} <{srv.get_name()}>')
            else:
                log(f'    C --> S {method_str} <{srv.get_name()}>')

            writer.write(self.construct_message(msg))
            await writer.drain()

    async def dispatch(self, msg, stdout_writer, server):
        from_server = server is not None

        if from_server:
            await self.process(server, stdout_writer, msg, from_server,
                    preserved_common_requests + preserved_server_client_notifications)
        else:
            for srv in self.servers:
                if srv.is_connected():
                    await self.process(srv, srv.get_stream_writer(), msg, from_server,
                            preserved_common_requests + preserved_client_server_notifications)

    def any_connected(self):
        return any([srv.is_connected() for srv in self.servers])

    def get_server_for_task(self, task):
        return next((srv for srv in self.servers if srv.task == task), None)

    def terminate_all(self):
        for srv in self.servers:
            srv.disconnect()

    # see https://stackoverflow.com/questions/64303607/python-asyncio-how-to-read-stdin-and-write-to-stdout
    async def connect_stdin_stdout(self):
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        w_transport, w_protocol = await loop.connect_write_pipe(asyncio.streams.FlowControlMixin, sys.stdout)
        writer = asyncio.StreamWriter(w_transport, w_protocol, reader, loop)
        return reader, writer

    async def main_loop(self):
        stdin_reader, stdout_writer = await self.connect_stdin_stdout()

        for srv in self.servers:
            success = await srv.connect()
            if not success:
                log('Failed to connect LSP server, terminating lsp-proxy')
                self.terminate_all()
                sys.exit(1)
            srv.reset_task()

        tasks = [x.task for x in self.servers]
        # the task for reading proxy's stdin is always at the end
        tasks.append(asyncio.create_task(read_message(None, stdin_reader)))

        while self.any_connected():
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

            for d in done:
                msg = d.result()
                if msg:
                    await self.dispatch(msg, stdout_writer, self.get_server_for_task(d))

            # create new tasks for "done" tasks but reuse the remaining ones
            for srv in self.servers:
                if srv.is_connected() and srv.get_stream_reader().at_eof():
                    await srv.wait_for_completion()
                elif srv.task in done:
                    srv.reset_task()

            stdin_task = tasks[-1]
            tasks = [srv.task for srv in self.servers if srv.is_connected()]

            # add task for proxy's stdin
            if stdin_task in done:
                tasks.append(asyncio.create_task(read_message(None, stdin_reader)))
            else:
                tasks.append(stdin_task)


def load_config(cfg):
    is_primary = True
    servers = []

    for srv_cfg in cfg:
        if 'cmd' in srv_cfg:
            args = []
            if 'args' in srv_cfg:
                args = srv_cfg['args']
                if not isinstance(args, list):
                    raise ValueError('"args" must be an array of arguments (strings)')
            srv = StdioServer(srv_cfg['cmd'], args, is_primary)
        elif 'port' in srv_cfg:
            port = srv_cfg['port']
            if not isinstance(port, int):
                raise ValueError('"port" must be an integer')
            host = "127.0.0.1"
            if 'host' in srv_cfg:
                host = srv_cfg['host']
            srv = SocketServer(host, port, is_primary)
        else:
            raise ValueError('Either "cmd" or "port" missing in server configuration')

        if 'initializationOptions' in srv_cfg:
            srv.initialization_options = srv_cfg['initializationOptions']

        if 'useDiagnostics' in srv_cfg:
            srv.use_diagnostics = srv_cfg['useDiagnostics']

        servers.append(srv)
        is_primary = False

    return servers


def signal_handler(signum, frame):
    proxy.terminate_all()


parser = argparse.ArgumentParser()
parser.add_argument("config_file", help="configuration file")
args = parser.parse_args()

try:
    with open(args.config_file, 'r') as file:
        data = file.read()
        cfg = json.loads(data)
        servers = load_config(cfg)
except (IOError, ValueError) as e:
    log(f'Failed to read JSON config file: {e}')
    sys.exit(1)

proxy = Proxy(servers)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

asyncio.run(proxy.main_loop())
