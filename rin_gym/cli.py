"""Installed forecast and evaluation commands."""
import argparse
import importlib
from importlib.metadata import version
import os
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version=version('rin-gym'))
    parser.add_argument('--workspace', type=Path)
    groups = parser.add_subparsers(dest='group', required=True)
    from app.forecast.eval_cli import add_commands
    add_commands(groups)
    forecast = groups.add_parser('forecast', help='Prepare, evaluate and simulate forecasts')
    commands = forecast.add_subparsers(dest='command', required=True)
    for name in ['prepare', 'run', 'verify']:
        commands.add_parser(name)
    simulate = commands.add_parser('simulate')
    simulate.add_argument('--match', required=True)
    simulate.add_argument('--utc', required=True)
    simulate.add_argument('--runs', type=int, default=10000)
    args = parser.parse_args(argv)
    if args.workspace is not None:
        os.environ['RIN_GYM_WORKSPACE'] = str(args.workspace.expanduser().resolve())
    try:
        if args.group == 'eval':
            from app.forecast.eval_cli import dispatch
            return dispatch(args)
        if args.command == 'simulate':
            if args.runs < 1:
                parser.error('--runs must be positive')
            from app.forecast.model import demo
            demo(['--match', args.match, '--utc', args.utc, '--runs', str(args.runs)])
        else:
            module, function = {'prepare': ('data', 'prepare'), 'run': ('evaluate', 'run'), 'verify': ('verify', 'main')}[args.command]
            getattr(importlib.import_module('app.forecast.' + module), function)()
    except FileNotFoundError as exc:
        parser.exit(1, f'Missing input: {exc.filename}. Use the same workspace for each step.\n')
    except ValueError as exc:
        parser.exit(1, f'{exc}\n')
    return 0
