#!/usr/bin/env python
"""Main entry point for TodoList tool."""

import sys


def main():
    """Main function - delegate to CLI."""
    from todo.cli import run_cli
    run_cli()


if __name__ == "__main__":
    main()
