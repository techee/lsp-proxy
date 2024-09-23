lsp-proxy
=========

lsp-proxy is a Python script that acts as a proxy between a LSP client and
one or more LSP servers. This gives the possibility to run multiple LSP
servers to clients that support only a single LSP server for one programming
language.

More specifically:
- the proxy declares one server as primary - primary server receives all
  requests and notifications and all its responses are returned back to the
  client. The primary server serves as the main server for all LSP features like
  autocompletion, goto, document symbols, etc.
- other servers receive only basic lifecycle and document synchronization
  messages. These allow the servers send correct
  `textDocument/publishDiagnostics` notifications which are all merged and
  sent back to the client. This means that non-primary servers can serve as
  linters and all error messages and warnings from them can be displayed by the
  client
- `initialize` and `shutdown` requests are synchronized so the request results
  are sent to the client only after all servers return a response. In addition,
  the client receives the result of `initialize` from the primary server only

Usage
-----

At the moment, there is no configuration file and child servers are configured
directly in the script:
```
# servers to start - command, arguments, is_primary
servers = [
    Server('jedi-language-server', [], True),
    Server('ruff', ['server'], False),
]
```
Afterwards, the script can be made executable or started using
```
python3 lsp-proxy.py
```
and configured in your editor as the language server executable.

Possible future improvements
----------------------------
- configuration file
- support for connecting servers using sockets. This would allow clients
  supporting only `stdin/stdout` communication work with servers supporting
  only socket communication
- general multiserver support where the proxy can be configured to e.g. use
  autocompletion from server 1, document symbols from server 2, goto
  from server 3. This would require merging all the server's `initialize`
  responses into one that contain all the features from all the clients and
  then dispatching the requests based on the configuration and server's
  capabilities

---

Jiri Techet, 2024
