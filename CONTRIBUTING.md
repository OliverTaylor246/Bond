# Contributing to Bond

Thank you for your interest in contributing to Bond!

## Development Setup

1. **Clone the repository**
   ```bash
   git clone <repo-url>
   cd bond
   ```

2. **Install dependencies**
   ```bash
   make install
   # or
   pip install -r requirements.txt
   ```

3. **Start local services**
   ```bash
   make up
   ```

4. **Run tests**
   ```bash
   make test
   ```

## Coding Standards

### Style Guide

- **Indentation**: 2 spaces (not tabs, not 4 spaces)
- **Naming conventions**:
  - Files/modules: `snake_case.py`
  - Classes: `PascalCase`
  - Functions/variables: `snake_case`
- **Type hints**: Mandatory for all public functions
- **Async/await**: Use for all I/O operations
- **Line length**: Max 100 characters (soft limit)

### Example

```python
# Good
async def create_stream(
  stream_id: str,
  spec: StreamSpec
) -> dict[str, Any]:
  """Create a new stream."""
  result = await runtime.launch_stream(stream_id, spec)
  return result

# Bad (no type hints, not async, 4-space indent)
def create_stream(stream_id, spec):
    result = runtime.launch_stream(stream_id, spec)
    return result
```

## Code Review Checklist

Before submitting a PR:

- [ ] Code follows style guide (2 spaces, type hints)
- [ ] All tests pass (`make test`)
- [ ] Linters pass (`make lint`)
- [ ] New code has tests
- [ ] Documentation updated if needed
- [ ] No security issues introduced

## Architecture Guidelines

### DO NOT modify without justification:

1. **`engine/schemas.py`** - Standard event model
   - Breaking changes require backward compatibility plan

2. **`apps/api/crypto.py`** - Security-critical auth logic
   - Requires security review before changes

3. **`StreamSpec` JSON DSL** - Stream specification schema
   - Must maintain backward compatibility

### Performance requirements:

- Pipeline latency must stay under **500ms** for test loads
- Use async/await for all I/O
- Avoid blocking operations in event loop

### Adding new connectors:

1. Create module in `connectors/`
2. Implement async generator yielding event dicts
3. Follow existing connector patterns
4. Update `README.md` and add to `connectors/README_CONNECTORS.md`
5. Add tests

## Testing

### Run all tests
```bash
make test
```

### Run specific test file
```bash
pytest tests/test_compiler.py -v
```

### Run with coverage
```bash
pytest --cov=apps --cov=engine --cov=connectors
```

## Submitting Changes

1. **Create a feature branch**
   ```bash
   git checkout -b feature/my-new-feature
   ```

2. **Make your changes**
   - Follow coding standards
   - Add tests
   - Update documentation

3. **Commit with clear messages**
   ```bash
   git commit -m "Add support for custom aggregation functions"
   ```

4. **Push and create PR**
   ```bash
   git push origin feature/my-new-feature
   ```

5. **PR checklist**:
   - Clear description of changes
   - Tests pass
   - Linters pass
   - Documentation updated
   - No merge conflicts

## AI Agent Development

This project uses Claude Code as a development assistant.

### Using Claude Code

```bash
claude
```

See [CLAUDE.md](CLAUDE.md) for detailed AI agent guidelines.

### Claude Code Commands

- `/init` - Generate CLAUDE.md if missing
- `/help` - Get help

## Questions?

- Open an issue for bugs or feature requests
- Tag with appropriate labels
- Be specific and provide examples

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
