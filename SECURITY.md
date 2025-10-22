# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.2.x   | :white_check_mark: |
| 0.1.x   | :x:                |

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, please report them via email to: **security@bondstreams.io**

You should receive a response within 48 hours. If for some reason you do not, please follow up via email to ensure we received your original message.

Please include the following information:

- Type of issue (e.g., buffer overflow, SQL injection, cross-site scripting, etc.)
- Full paths of source file(s) related to the manifestation of the issue
- The location of the affected source code (tag/branch/commit or direct URL)
- Any special configuration required to reproduce the issue
- Step-by-step instructions to reproduce the issue
- Proof-of-concept or exploit code (if possible)
- Impact of the issue, including how an attacker might exploit it

## Security Best Practices

### For Local Development

1. **Never commit secrets**
   - Always use `.env` files (excluded by `.gitignore`)
   - Use `.env.example` as a template
   - Run `git secrets --scan` before committing

2. **Change default credentials**
   - Update `BOND_SECRET` in production
   - Use strong, randomly-generated values
   - Rotate secrets regularly

3. **Token management**
   - Tokens expire after 1 hour by default
   - Use short-lived tokens in production
   - Never share tokens in URLs or logs

### For Production Deployment

1. **Use HTTPS/WSS**
   - Never use unencrypted connections
   - Use valid TLS certificates
   - Enable HSTS headers

2. **Network isolation**
   - Keep Redis on private network
   - Use firewall rules to restrict access
   - Enable Redis authentication

3. **Rate limiting**
   - Implement rate limiting at API gateway
   - Monitor for abuse patterns
   - Set appropriate stream limits

4. **Data compliance**
   - Ensure compliance with data source ToS
   - Do not redistribute market data
   - Respect exchange rate limits

## Known Security Considerations

### Authentication

- HMAC-based token authentication (apps/api/crypto.py)
- Tokens are signed with `BOND_SECRET`
- Default TTL: 3600 seconds (configurable)
- **Do not modify crypto.py without security review**

### Data Sources

- CCXT: REST API polling (no credentials stored)
- Twitter/X: Optional bearer token (stored in environment)
- On-chain gRPC: Endpoint configuration only

### Container Security

- Run containers as non-root user
- Use minimal base images
- Scan images for vulnerabilities
- Keep dependencies updated

## Responsible Disclosure

We appreciate responsible disclosure of security vulnerabilities. We will:

1. Confirm receipt of your vulnerability report
2. Investigate and validate the issue
3. Work on a fix and coordinate disclosure timeline
4. Credit you in release notes (if desired)

Thank you for helping keep Bond and our users safe!
