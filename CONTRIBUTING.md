# Contributing to RUNECLAW

Thank you for your interest in contributing to RUNECLAW.

## How to Contribute

1. **Fork** the repository on GitHub.
2. **Create a branch** for your change: `git checkout -b my-feature`
3. **Make your changes** and add tests where applicable.
4. **Run the test suite** before submitting:
   ```bash
   python -m pytest tests/ -v
   ```
5. **Commit** with a clear, descriptive message.
6. **Open a Pull Request** against the `main` branch.

## Code Style

- Python 3.11+
- Follow existing code conventions in the repository.
- Use type hints where practical.
- Use Pydantic models for structured data.
- Keep functions focused and well-documented.

## Testing

- All new features should include tests.
- Tests live in the `tests/` directory.
- Run the full suite with `python -m pytest tests/`.

## Reporting Issues

Open a GitHub issue with a clear description, steps to reproduce, and expected vs. actual behavior.

## License

By contributing, you agree that your contributions will be licensed under the
Business Source License 1.1 (BUSL-1.1), the same license as the project, and
that Humanoid Traders may relicense the Licensed Work (including your
contributions) under the Change License or a commercial license as described in
[LICENSE](./LICENSE).
