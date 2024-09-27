lsp-proxy
=========

lsp-proxy is a Python script that acts as a proxy between a LSP client and
one or more LSP servers. This gives the possibility to run multiple LSP
servers to clients that support only a single LSP server for one programming
language or to use socket server connection when clients support only
stdio-based communication.

Technical details:
- the proxy declares one server as primary - the primary server receives all
  requests and notifications and all its responses are returned back to the
  client. The primary server serves as the main server for all LSP features like
  autocompletion, goto, document symbols, etc.
- other servers receive only basic lifecycle and document synchronization
  messages. These allow the non-primary servers to send correct
  `textDocument/publishDiagnostics` notifications which are all merged and
  sent back to the client. This means that non-primary servers can serve as
  linters or error checkers
- `initialize` and `shutdown` requests are synchronized so the request results
  are sent to the client only after all servers return a response. In addition,
  the client receives the result of `initialize` from the primary server only
- `initializationOptions` from the `initialize` request are sent only to the
  primary server; they are removed for all other servers

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

The script can be made executable or started using
```
python3 lsp-proxy.py <config_file>
```
and configured in your editor as the LSP server executable taking the
configuration file as its argument.

Possible future improvements
----------------------------
- configurable server initialization options for all the servers
- general multiserver support where the proxy can be configured to e.g. use
  autocompletion from server 1, document symbols from server 2, goto
  from server 3. This would require merging all the server's `initialize`
  responses into one that contains all the features from all the clients and
  then dispatching the requests based on the configuration and server's
  capabilities

---

Jiri Techet, 2024
