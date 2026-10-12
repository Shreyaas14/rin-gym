import argparse
from .data import prepare

parser = argparse.ArgumentParser(description='World Cup chronological forecast benchmark')
parser.add_argument('command', choices=['prepare', 'run', 'simulate'])
args, extra = parser.parse_known_args()
if extra and args.command != 'simulate':
    parser.error('Unexpected arguments: ' + ' '.join(extra))
if args.command == 'prepare':
    prepare()
elif args.command == 'run':
    from .evaluate import run
    run()
else:
    from .model import demo
    demo(extra)
