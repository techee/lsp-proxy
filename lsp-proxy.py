#!/usr/bin/env python3
#
# Author:  Jiri Techet, 2024
# License: GPL v2 or later

from abc import ABC, abstractmethod
import asyncio
import json
import signal
import sys


class Server(ABC):
    def __init__(self, is_primary):
        self.pending_client_server_requests = {}
        self.pending_server_client_requests = {}
        self.is_primary = is_primary
        self.initialize_msg = None
        self.shutdown_received = False
        self.diagnostics = {}

    def reset_task(self):
        self.task = asyncio.create_task(read_message(self, self.get_stream_reader()))

    @abstractmethod
    async def connect(self):
        pass

    @abstractmethod
    def disconnect(self):
        pass

    @abstractmethod
    def is_connected(self):
        pass

    @abstractmethod
    async def wait_for_completion(self):
        pass

    @abstractmethod
    def get_stream_reader(self):
        pass

    @abstractmethod
    def get_stream_writer(self):
        pass

    @abstractmethod
    def get_name(self):
        pass


class StdioServer(Server):
    def __init__(self, cmd, args, primary):
        super().__init__(primary)
        self.cmd = cmd
        self.args = args
        self.proc = None

    async def connect(self):
        try:
            self.proc = await asyncio.create_subprocess_exec(self.cmd, *self.args,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
            return True
        except FileNotFoundError as err:
            log(err)
        return False

    def is_connected(self):
        return self.proc.returncode is None

    def disconnect(self):
        if self.proc and self.is_connected():
            self.proc.terminate()

    async def wait_for_completion(self):
        await self.proc.wait()

    def get_stream_reader(self):
        return self.proc.stdout

    def get_stream_writer(self):
        return self.proc.stdin

    def get_name(self):
        return self.cmd


class SocketServer(Server):
    def __init__(self, host, port, primary):
        super().__init__(primary)
        self.host = host
        self.port = port

    async def connect(self):
        try:
            self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
            return True
        except ConnectionRefusedError as err:
            log(err)
        return False

    def is_connected(self):
        return not self.writer.is_closing()

    def disconnect(self):
        if self.writer and self.is_connected():
            self.writer.close()

    async def wait_for_completion(self):
        self.writer.close()
        await self.writer.wait_closed()

    def get_stream_reader(self):
        return self.reader

    def get_stream_writer(self):
        return self.writer

    def get_name(self):
        return f'{self.host}:{self.port}'


# - all messages to/from the primary server (should be just one) are sent to/from the client
# - for non-primary servers, only initialize, shutdown, document synchronization,
#   diagnostic and logging messages are sent
# - diagnostics are merged from all servers, other messages are left intact
servers = [
    # servers to start - command, arguments, is_primary
    StdioServer('jedi-language-server', [], True),
    StdioServer('ruff', ['server'], False),

    # servers to connect over TCP: hostname, port, is_primary
    # e.g. for externally started 'pylsp --tcp --port 8888'
    #SocketServer('127.0.0.1', 8888, True),
]


kept_common_requests = ['initialize', 'shutdown', 'window/workDoneProgress/create',
    'window/workDoneProgress/cancel']
kept_client_server_notifications = ['initialized', 'exit',
    'textDocument/didOpen', 'textDocument/didChange', 'textDocument/didSave', 'textDocument/didClose',
    'workspace/didChangeWorkspaceFolders']
kept_server_client_notifications = ['textDocument/publishDiagnostics',
    'window/showMessage', 'window/logMessage']

initialize_id = -1
shutdown_id = -1


def log(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


# see https://stackoverflow.com/questions/64303607/python-asyncio-how-to-read-stdin-and-write-to-stdout
async def connect_stdin_stdout():
    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)
    w_transport, w_protocol = await loop.connect_write_pipe(asyncio.streams.FlowControlMixin, sys.stdout)
    writer = asyncio.StreamWriter(w_transport, w_protocol, reader, loop)
    return reader, writer


def filter_msg(method, iden, is_primary, kept_methods):
    if is_primary:
        return False

    return method not in kept_methods


def all_initialized():
    return all([srv.initialize_msg for srv in servers])


def all_shutdown():
    return all([srv.shutdown_received for srv in servers])


def get_primary():
    return next((srv for srv in servers if srv.is_primary), servers[0])


def get_merged_diagnostics(uri):
    diags = []
    for srv in servers:
        if uri in srv.diagnostics:
            diags += srv.diagnostics[uri]
    return diags


def construct_message(msg):
    msg_str = json.dumps(msg).encode('utf-8')
    return b'Content-Length: ' + str(len(msg_str)).encode('utf-8') + b'\r\n\r\n' + msg_str


async def process(srv, writer, msg, from_server, kept_methods):
    global initialize_id, shutdown_id

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
    elif not filter_msg(method, iden, srv.is_primary, kept_methods):
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
        if iden == initialize_id:
            srv.initialize_msg = msg
            should_send = all_initialized()
            # send initialize response only when all servers returned response
            if should_send:
                # send the primary server's initialize response
                msg = get_primary().initialize_msg
        elif iden == shutdown_id:
            srv.shutdown_received = True
            # send shutdown response only when all servers returned response
            should_send = all_shutdown()
    else:
        if method == 'initialize':
            initialize_id = iden
            if not srv.is_primary and 'initializationOptions' in msg['params']:
                msg['params']['initializationOptions'] = None
        elif method == 'shutdown':
            shutdown_id = iden

    if should_send:
        method_str = method if method else "no method"
        if from_server:
            if method == 'textDocument/publishDiagnostics':
                uri = msg['params']['uri']
                srv.diagnostics[uri] = msg['params']['diagnostics']
                # modify msg to contain diagnostics from all servers
                msg['params']['diagnostics'] = get_merged_diagnostics(uri)
            log(f'    C <-- S {method_str} <{srv.get_name()}>')
        else:
            log(f'    C --> S {method_str} <{srv.get_name()}>')

        writer.write(construct_message(msg))
        await writer.drain()


async def dispatch(msg, stdout_writer, server):
    from_server = server is not None

    if from_server:
        await process(server, stdout_writer, msg, from_server,
                kept_common_requests + kept_server_client_notifications)
    else:
        for srv in servers:
            if srv.is_connected():
                await process(srv, srv.get_stream_writer(), msg, from_server,
                        kept_common_requests + kept_client_server_notifications)


async def read_message(srv, stream):
    try:
        # HTTP-like header separated by newline
        header = await stream.readuntil(b'\r\n\r\n')
    except asyncio.exceptions.IncompleteReadError:
        if not srv.shutdown_received and srv.is_connected() and not srv.get_stream_reader().at_eof():
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


def any_connected():
    return any([srv.is_connected() for srv in servers])


def get_server_for_task(task):
    return next((srv for srv in servers if srv.task == task), None)


def terminate_all():
    for srv in servers:
        srv.disconnect()


async def main_loop():
    stdin_reader, stdout_writer = await connect_stdin_stdout()

    for srv in servers:
        success = await srv.connect()
        if not success:
            log('Failed to connect LSP server, terminating lsp-proxy')
            terminate_all()
            sys.exit(1)
        srv.reset_task()

    tasks = [x.task for x in servers]
    # the task for reading proxy's stdin is always at the end
    tasks.append(asyncio.create_task(read_message(None, stdin_reader)))

    while any_connected():
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

        for d in done:
            msg = d.result()
            if msg:
                await dispatch(msg, stdout_writer, get_server_for_task(d))

        # create new tasks for "done" tasks but reuse the remaining ones
        for srv in servers:
            if srv.is_connected() and srv.get_stream_reader().at_eof():
                await srv.wait_for_completion()
            elif srv.task in done:
                srv.reset_task()

        stdin_task = tasks[-1]
        tasks = [srv.task for srv in servers if srv.is_connected()]

        # add task for proxy's stdin
        if stdin_task in done:
            tasks.append(asyncio.create_task(read_message(None, stdin_reader)))
        else:
            tasks.append(stdin_task)


def signal_handler(signum, frame):
    terminate_all()


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

asyncio.run(main_loop())
