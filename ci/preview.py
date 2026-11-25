from pathlib import Path, PurePosixPath
import argparse, urllib.request
root=Path(__file__).absolute().parents[1]
ap=argparse.ArgumentParser();ap.add_argument('source');args=ap.parse_args()
p=PurePosixPath(args.source)
if not p.parts or p.parts[0]!='src' or '..' in p.parts or p.suffix!='.puml' or not (root/p).is_file():
    raise SystemExit('Specify an existing src/*.puml entry point')
text='@startuml\n!include /workspace/'+p.as_posix()+'\n@enduml'
request=urllib.request.Request('http://127.0.0.1:18080/svg',data=text.encode(),headers={'Content-Type':'text/plain; charset=utf-8'})
with urllib.request.urlopen(request,timeout=60) as response:data=response.read()
if b'<svg' not in data or b'Syntax Error?' in data:raise SystemExit('Preview failed')
target=root/'.build/preview'/p.with_suffix('.svg');target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
print(target)
