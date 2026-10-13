"""One installed command for acquisition, replay, forecasts and evaluation."""
import argparse
from contextlib import contextmanager
import importlib
from importlib.metadata import version
import os
import sys
from pathlib import Path

PIPELINES = {
    'audit': ('run_audit', 'run'),
    'collect': ('collect', 'collect'),
    'analyze': ('analyze', 'run'),
    'verify': ('verify', 'main'),
    'fifa-census': ('fifa_census', 'main'),
    'fifa-join': ('fifa_join', 'main'),
}
DELEGATES = {
    'fifa': 'pipelines.fifa.__main__',
    'markets': 'pipelines.fifa.markets',
    'market-history': 'pipelines.fifa.market_history',
    'verify-markets': 'pipelines.fifa.verify_markets',
}


@contextmanager
def selected_directory():
    """Relative data/output arguments are rooted in the selected workspace."""
    from .paths import workspace_root
    previous = Path.cwd()
    root = workspace_root()
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chdir(root)
        yield
    finally:
        os.chdir(previous)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version=version('rin-gym'))
    parser.add_argument('--workspace', type=Path, help='Data/output root; defaults to RIN_GYM_WORKSPACE or current directory')
    groups = parser.add_subparsers(dest='group', required=True)
    from app.forecast.eval_cli import add_commands
    add_commands(groups)
    pipeline = groups.add_parser('pipeline', help='Acquire, map and validate sports data')
    commands = pipeline.add_subparsers(dest='command', required=True)
    for name in PIPELINES:
        commands.add_parser(name)
    for name in DELEGATES:
        delegated = commands.add_parser(name, add_help=False)
        delegated.add_argument('arguments', nargs=argparse.REMAINDER)
    replay = groups.add_parser('replay', add_help=False, help='Import, inspect, verify and render arbitrary FIFA games')
    replay.add_argument('arguments', nargs=argparse.REMAINDER)
    forecast = groups.add_parser('forecast', help='Prepare, evaluate and simulate forecasts')
    commands = forecast.add_subparsers(dest='command', required=True)
    for name in ['prepare', 'run', 'verify']:
        commands.add_parser(name)
    simulate = commands.add_parser('simulate')
    simulate.add_argument('--match', required=True)
    simulate.add_argument('--utc', required=True)
    simulate.add_argument('--runs', type=int, default=10000)
    detailed = groups.add_parser('play-by-play', help='Build a StatsBomb event and 360 replay')
    detailed.add_argument('--match', type=int, default=3943043)
    detailed.add_argument('--competition', type=int, default=55)
    detailed.add_argument('--season', type=int, default=282)
    detailed.add_argument('--output', type=Path)
    detailed.add_argument('--market-slug')
    tokens = list(sys.argv[1:] if argv is None else argv)
    args, remaining = parser.parse_known_args(tokens)
    delegated = args.group == 'replay' or (args.group == 'pipeline' and args.command in DELEGATES)
    if remaining and not delegated:
        parser.error('unrecognized arguments: ' + ' '.join(remaining))
    if args.workspace is not None:
        os.environ['RIN_GYM_WORKSPACE'] = str(args.workspace.expanduser().resolve())
    try:
        if delegated:
            # Preserve ordering of delegated options; argparse's unknown-option
            # list would move '--workers' behind its value, for example.
            index = 0
            while index < len(tokens):
                if tokens[index] == '--workspace':
                    index += 2
                elif tokens[index].startswith('--workspace='):
                    index += 1
                else:
                    break
            options = tokens[index + (2 if args.group == 'pipeline' else 1):]
            module = 'app.replay.__main__' if args.group == 'replay' else DELEGATES[args.command]
            function = importlib.import_module(module).main
            # Help must remain usable without a workspace or input data.
            if '--help' in options or '-h' in options:
                function(options)
            else:
                with selected_directory():
                    function(options)
        elif args.group == 'eval':
            from app.forecast.eval_cli import dispatch
            return dispatch(args)
        elif args.group == 'pipeline':
            module, function = PIPELINES[args.command]
            getattr(importlib.import_module('pipelines.sports_data.' + module), function)()
        elif args.group == 'play-by-play':
            from app.play_by_play.build import build
            build(args.match, args.competition, args.season, args.output, args.market_slug)
        elif args.command == 'simulate':
            if args.runs < 1:
                parser.error('--runs must be positive')
            from app.forecast.model import demo
            demo(['--match', args.match, '--utc', args.utc, '--runs', str(args.runs)])
        else:
            module, function = {'prepare': ('data', 'prepare'), 'run': ('evaluate', 'run'), 'verify': ('verify', 'main')}[args.command]
            getattr(importlib.import_module('app.forecast.' + module), function)()
    except FileNotFoundError as exc:
        parser.exit(1, f'Missing input: {exc.filename}. Use the same --workspace for acquisition and replay.\n')
    except (ValueError, RuntimeError) as exc:
        parser.exit(1, f'{exc}\n')
    return 0
