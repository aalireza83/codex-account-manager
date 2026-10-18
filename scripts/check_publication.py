#!/usr/bin/env python3
"""Focused checks for the public source tree; matched credentials are never printed."""
import ast
import json
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
REQUIRED=(
    'README.md','LICENSE','CONTRIBUTING.md','SECURITY.md','THIRD_PARTY_NOTICES.md',
    '.github/workflow-templates/checks.yml','.github/pull_request_template.md',
    'docs/deployment.md','deploy/nginx.conf','deploy/nginx-proxy.conf',
    'deploy/codex-account-manager.system.service',
    'web/vendor/tailadmin/LICENSE','web/vendor/OUTFIT-LICENSE','web/vendor/LOBE-ICONS-LICENSE',
)
SECRET_PATTERNS={
    'private-key':re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'github-token':re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})'),
    'api-key':re.compile(r'\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}'),
}
PRIVATE_NAMES={'auth.json','accounts.json','registry.json','config.json','secret.env','.env'}
TEXT_SUFFIXES={'.py','.js','.css','.html','.md','.json','.toml','.yml','.yaml','.service','.conf','.svg'}
errors=[]

def fail(message):errors.append(message)


def candidate_paths():
    result=subprocess.run(['git','ls-files','--cached','--others','--exclude-standard','-z'],
                          cwd=ROOT,check=True,capture_output=True,text=True)
    return sorted({Path(name) for name in result.stdout.split('\0') if name and (ROOT/name).is_file()})


class Links(HTMLParser):
    def __init__(self):super().__init__();self.assets=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        for key in ('src','href'):
            if attrs.get(key,'').startswith('/assets/'):
                self.assets.append(attrs[key].split('?',1)[0])


for name in REQUIRED:
    if not (ROOT/name).is_file():fail('Missing publication file: '+name)

for relative in candidate_paths():
    path=ROOT/relative
    if relative.name in PRIVATE_NAMES or relative.name.endswith(('.auth.json','.pem','.key','.p12','.pfx','.htpasswd','.web.runtime.json')):
        fail('Private file in publication candidates: '+str(relative))
    try:text=path.read_text(encoding='utf-8')
    except UnicodeError:continue
    for kind,pattern in SECRET_PATTERNS.items():
        for match in pattern.finditer(text):
            line=text.count('\n',0,match.start())+1
            fail(f'{kind} pattern in {relative}:{line} (value hidden)')
    if 'vendor' not in relative.parts and (relative.suffix in TEXT_SUFFIXES or relative.name in ('LICENSE','.gitignore')):
        for line_number,line in enumerate(text.splitlines(),1):
            # Authored prose is English; symbols, punctuation, and emoji are allowed.
            if re.search(r'[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff]',line):
                fail(f'Non-English authored text in {relative}:{line_number}')
    if relative.parts[0] in ('deploy',) or relative in (Path('README.md'),Path('docs/deployment.md')):
        if re.search(r'/root/|aalireza\.pro|\.codex-auth-webui\.htpasswd',text):
            fail('Personal deployment identifier in '+str(relative))
    if relative.suffix=='.py':
        try:ast.parse(text,filename=str(relative))
        except SyntaxError:fail('Invalid Python syntax in '+str(relative))

for filename in ('package.json','package-lock.json'):
    data=json.loads((ROOT/filename).read_text())
    if data.get('license')!='MIT':fail('Unexpected package license in '+filename)
license_text=(ROOT/'LICENSE').read_text() if (ROOT/'LICENSE').exists() else ''
if not license_text.startswith('MIT License') or not re.search(r'Copyright \(c\) \d{4} \S.+',license_text):
    fail('Project license or copyright notice is missing')

for filename in ('README.md','CONTRIBUTING.md','SECURITY.md','THIRD_PARTY_NOTICES.md','docs/deployment.md'):
    text=(ROOT/filename).read_text()
    for link in re.findall(r'\[[^\]]*\]\(([^)]+)\)',text):
        target=link.split('#',1)[0]
        if not target or '://' in target or target.startswith('mailto:'):continue
        if not (ROOT/filename).parent.joinpath(target).exists():
            fail(f'Broken local link in {filename}: {target}')

links=Links();links.feed((ROOT/'web/index.html').read_text())
for route in links.assets:
    if not (ROOT/'web'/route.lstrip('/')).is_file():fail('Missing bundled asset: '+route)

# The removed legacy drop-in must not point public installs at private paths.
if (ROOT/'codex-app-server-webui-profile.conf').exists():fail('Obsolete server-specific drop-in is still present')

if errors:
    for error in errors:print(error,file=sys.stderr)
    raise SystemExit(1)
print('PASS: publication files, English authored text, generic deployment templates, asset/doc links, Python syntax, and common credential patterns.')
