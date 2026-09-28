#!/usr/bin/env python3
"""Run on the server interactively; credentials never appear in shell history."""
import getpass
import json
import os
from pathlib import Path

path=Path('/root/.config/aquant/mail.json')
if not path.exists(): raise SystemExit('Create the private mail template first')
password=getpass.getpass('Gmail app password (hidden input): ').replace(' ','').strip()
if len(password)!=16: raise SystemExit('Expected a 16-character Gmail app password; configuration unchanged')
cfg=json.loads(path.read_text()); cfg['SMTP_PASSWORD']=password
os.umask(0o077)
path.write_text(json.dumps(cfg,indent=2)); path.chmod(0o600)
print('Private mail configuration saved. Run scripts/test_mail.py to test delivery.')
