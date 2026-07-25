# Security policy

## Supported versions

Security fixes are provided for the latest published release. Users should upgrade to the newest version before reporting an issue that may already be fixed.

## Report a vulnerability

Do not open a public GitHub issue for suspected vulnerabilities.

Email `joe.blazick@yahoo.com` with the subject `SECURITY: arxiv-mcp-server`. Include:

- affected version and installation method
- impact and attack scenario
- minimal reproduction steps or proof of concept
- suggested mitigation, if known

Reports are handled on a best-effort basis. This is an open-source project maintained in spare time and has no guaranteed response SLA.

## Threat model

### Untrusted paper content

Paper text, abstracts, metadata, and LaTeX source are external input. They can contain prompt-injection text intended to manipulate an MCP client or model into ignoring its instructions or calling unrelated tools.

The server marks and bounds content where practical, but it cannot prevent a client model from following malicious text. Clients should:

- keep approval controls enabled for shell, filesystem, browser, messaging, and other sensitive tools
- treat instructions found inside papers as data, not commands
- review model output before external actions
- sandbox automated pipelines that combine paper content with privileged tools

### LaTeX source archives

Source archives are treated as hostile. The server validates archive paths and member types, rejects links and duplicate normalized paths, enforces compressed and expanded-size limits, caps include recursion and aggregate output, and writes cache records atomically. These controls reduce archive traversal and decompression risks but do not make source content trustworthy.

### Streamable HTTP

Streamable HTTP binds to loopback by default and enables DNS-rebinding protection. If the server is exposed through a reverse proxy, provide authentication and network controls upstream, keep the process on a private interface, and configure `ALLOWED_HOSTS` and `ALLOWED_ORIGINS` for forwarded values.

## Secrets and private data

The server does not require an arXiv API key. Do not include credentials, private paper collections, local indexes, or sensitive filesystem paths in issues, logs, or pull requests.
