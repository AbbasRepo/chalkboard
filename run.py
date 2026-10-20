"""Start everything with one command.

Judges will not open four terminals. This starts the generator, loader,
significance watcher, MCP server and API as subprocesses and shuts them all
down together on Ctrl-C.

    python run.py --seed 42 --speed 20

TODO(WP4): wire up once the components exist.
"""
