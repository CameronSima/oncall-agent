"""Allow `python -m oncall_agent ...` as an alias for the CLI."""
from .cli import app

if __name__ == "__main__":
    app()
