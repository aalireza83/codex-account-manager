#!/usr/bin/env python3
"""Run with the manager stopped; source files are preserved."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from accounts import manager_config_path,manager_data_root
from migration import migrate_storage

parser=argparse.ArgumentParser(description='Copy existing accounts/settings into an independent Codex Account Manager vault')
parser.add_argument('--source-config',required=True,type=Path)
parser.add_argument('--source-accounts',required=True,type=Path)
parser.add_argument('--codex-home',type=Path,default=Path.home()/'.codex')
parser.add_argument('--target-config',type=Path,default=manager_config_path())
parser.add_argument('--target-data',type=Path,default=manager_data_root())
args=parser.parse_args()
try:
    result=migrate_storage(args.source_config,args.source_accounts,args.target_config,args.target_data,args.codex_home)
except (RuntimeError,OSError,ValueError) as error:
    parser.exit(1,f'Migration failed: {type(error).__name__}; original files preserved.\n')
print(json.dumps(result))
