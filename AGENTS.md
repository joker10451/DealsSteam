# Agent Guidelines for Game Deals Bot Repository

## Development Setup
- Python 3.9+
- Install dependencies: `pip install -r requirements.txt`
- Copy `.env.example` to `.env` and configure required variables

## Build Commands
- No traditional build step (pure Python)
- Database initialization: `python -c "from database import init_db; import asyncio; asyncio.run(init_db())"`

## Linting Commands
- Flake8: `flake8 .`
- Black formatter: `black .`
- Isort: `isort .`
- MyPy: `mypy .`

## Testing Commands
- All tests: `pytest`
- Verbose: `pytest -v`
- Specific test file: `pytest tests/test_database.py`
- Single test function: `pytest tests/test_database.py::test_function_name`
- With coverage: `pytest --cov=game_deals_bot`
- Keyword match: `pytest -k "price_parser"`

## Code Style Guidelines

### Formatting
- Follow PEP 8
- Line length: 88 characters (black default)
- 4 spaces per indentation level
- 2 blank lines between top-level definitions
- 1 blank line between method definitions

### Imports
- Order: standard library, third-party, local
- Blank line between groups
- Absolute imports from project root
- Specific imports preferred over wildcards

### Type Hints
- Use for all function parameters and returns
- Use built-in collections (list, dict) in Python 3.9+
- Optional[T] for nullable values
- Union[A, B] for multiple types

### Naming Conventions
- Variables/functions: `snake_case`
- Classes: `PascalCase`
- Constants: `UPPER_SNAKE_CASE`
- Descriptive names (avoid single letters except loop counters)
- Boolean positives: `is_valid`, `has_permission`

### Error Handling
- Catch specific exceptions, not bare `except:`
- Log with context using `logging.getLogger(__name__)`
- Use appropriate levels: DEBUG, INFO, WARNING, ERROR, CRITICAL
- Re-raise when unable to handle meaningfully

### Documentation
- Docstrings: triple double quotes, Google style
- Comments: explain why, not what
- Keep documentation synchronized with code

## Special Considerations

### Async Code
- Use `async`/`await` properly
- Avoid blocking calls in async functions
- Use `asyncio.gather()` for concurrency
- Manage resources with `async with`

### Database
- Use connection pooling (asyncpg)
- Parameterized queries to prevent SQL injection
- Explicit transaction handling
- Close connections properly

### External APIs
- Implement rate limiting
- Handle network errors gracefully
- Cache responses when appropriate
- Respect API terms of service

## Git Workflow
- Main branch: `main` (stable)
- Feature branches: `feature/short-description`
- Commit messages: imperative mood, <50 char subject
- Pull requests: focused, include tests, request review
- Tags: semantic versioning vMAJOR.MINOR.PATCH