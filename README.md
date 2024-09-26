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

Usage
-----

At the moment, there is no configuration file and child servers are configured
directly in the script:
```python
servers = [
    # servers to start - command, arguments, is_primary
    StdioServer('jedi-language-server', [], True),
    StdioServer('ruff', ['server'], False),

    # servers to connect over TCP: hostname, port, is_primary
    # e.g. for externally started 'pylsp --tcp --port 8888'
    #SocketServer('127.0.0.1', 8888, True),
]
```
Afterwards, the script can be made executable or started using
```
python3 lsp-proxy.py
```
and configured in your editor as the LSP server executable.

Possible future improvements
----------------------------
- configuration file (probably JSON-based to easily support the next point)
- configurable server initialization options for all the servers
- general multiserver support where the proxy can be configured to e.g. use
  autocompletion from server 1, document symbols from server 2, goto
  from server 3. This would require merging all the server's `initialize`
  responses into one that contains all the features from all the clients and
  then dispatching the requests based on the configuration and server's
  capabilities

---

Jiri Techet, 2024
