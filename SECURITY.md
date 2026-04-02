# Security Policy

## Supported Versions

Only the current release is actively supported with security updates.

| Version | Supported          |
| ------- | ------------------ |
| 0.3.2   | :white_check_mark: |
| < 0.3.2 | :x:                |

## Reporting a Vulnerability

If you discover a security vulnerability in arxiv-mcp-server, please report it by
sending an email to:

  joe.blazick@yahoo.com

Subject line: SECURITY: arxiv-mcp-server

Please include a description of the issue, steps to reproduce, and any relevant
details about your environment. Do not open a public GitHub issue for security
vulnerabilities.

Expected response time: best effort. This is a free open source project maintained
in spare time, so there is no guaranteed SLA, but reports will be taken seriously
and addressed as quickly as possible.

## Known Risks

### Prompt Injection via Paper Content

arXiv papers are user-generated, untrusted content. A maliciously crafted paper
could contain text designed to manipulate an AI assistant's behavior (prompt
injection). When this server returns paper content to an AI model, that content
should be treated as untrusted input.

Mitigations to consider:
- Run the MCP server in a sandboxed environment in production deployments.
- Be cautious when feeding paper content directly into agentic workflows with
  access to sensitive tools or data.
- Review paper content before using it in high-stakes automated pipelines.

This risk is inherent to any system that feeds external, user-generated text to
an AI model and cannot be fully eliminated by this server alone.
