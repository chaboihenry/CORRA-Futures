import yaml
from pathlib import Path

CONFIG = Path(__file__).parent.parent / 'config'

with open(CONFIG / 'graph_spec.yaml') as f:
    spec = yaml.safe_load(f)

print(list(spec.keys()))