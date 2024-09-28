lsp-proxy
=========

lsp-proxy is a Python script that acts as a proxy between a LSP client and
one or more LSP servers. This gives the possibility to run multiple LSP
servers to clients that support only a single LSP server for one programming
language or to use socket server connection when clients support only
stdio-based communication.

Technical details:
- the proxy declares one server as primary - unless configured differently,
  the primary server receives all requests and notifications and all its
  responses are returned back to the client. The primary server serves as the
  main server for all LSP features
- other servers receive only basic lifecycle and document synchronization
  messages. These allow the non-primary servers to send correct
  `textDocument/publishDiagnostics` notifications which are all merged and
  sent back to the client. This means that non-primary servers can serve as
  linters or error checkers
- instead of the primary server, some requests (currently only
  `textDocument/formatting`, `textDocument/rangeFormatting`,
  `textDocument/completion`, `completionItem/resolve`) can be
  dispatched to other servers based on the configuration or support
  of the particular feature by the server
- `initialize` and `shutdown` requests are synchronized so the request results
  are sent to the client only after all servers return a response. In addition,
  the client receives the result of `initialize` from the primary server only,
  modified by features used from other servers
- `initializationOptions` from the `initialize` request are sent selectively -
  see the `initializationOptions` configuration option below

Usage
-----

The server is configured using a simple JSON configuration file which is passed
as the command-line argument of the script. A simple configuration file
looks as follows:
```json
[
    {
        "cmd": "jedi-language-server"
    },
    {
        "cmd": "ruff",
        "args": ["server"]
    }
]
```
The first server in the array is primary. Valid configuration options are:
- `cmd` (mandatory when `port` is not present): the executable of the server
  to start when using stdin/stdout communication
- `args` (default `[]`): an array of command-line arguments of `cmd`
- `port` (mandatory when `cmd` is not present): port to connect when using
  socket-based communication. Note that when using sockets, the proxy does
  not start the server process and the process has to be started externally
  (e.g. by `pylsp --tcp --port 8888`)
- `host` (default `"127.0.0.1"`): hostname to connect when using socket-based
  communication
- `initializationOptions` (default `null`): the `initializationOptions` field
  of the `initialize` request that is passed to the server. This value is also
  returned when the client calls `workspace/didChangeConfiguration`. The exact
  format of this field is server-specific - consult the documentation of the
  server. If set to `null`, the proxy forwards the `initializationOptions`
  value from the client for the primary server and sets the value to `null` for
  all other servers

### Dispatching requests to non-primary server
Some requests, currently only formatting and completion, can be dispatched to
other server than the primary. Even when not configured explicitly, the proxy
checks the above-mentioned feature availability and if the primary server does
not support them, it uses the first configured server that does.

The following configuration options control this behavior:
- `useFormatting` (default `False`): when set to `True` and the configured
  server supports formatting, it becomes the server used for code formatting;
  otherwise, the first configured server supporting code formatting becomes the
  server used for formatting
- `useCompletion` (default `False`): when set to `True` and the configured
  server supports completion, it becomes the server used for completion;
  otherwise, the first configured server supporting completion becomes the
  server used for completion
- `useDiagnostics` (default `True`): whether to use diagnostics (errors,
  warnings) received using `textDocument/publishDiagnostics` from the server

The script can be made executable or started using
```
python3 lsp-proxy.py <config_file>
```
and configured in your editor as the LSP server executable taking the
configuration file as its argument.

Possible future improvements
----------------------------
- more configuration options for controlling which requests get dispatched to
  non-primary servers. Please create an issue if you run into this problem

---

Jiri Techet, 2024
