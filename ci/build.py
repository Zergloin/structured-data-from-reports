"""The same entry point runs locally and in GitHub Actions (stdlib only)."""
from pathlib import Path
import argparse, csv, hashlib, json, os, re, shutil, struct, subprocess, sys, time, urllib.request
import xml.etree.ElementTree as ET

ROOT=Path(__file__).absolute().parents[1]
class BuildError(RuntimeError): pass
def read(p): return p.read_text(encoding='utf-8-sig')
def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def arguments(s): return next(csv.reader([s],skipinitialspace=True))
def run(cmd, log, env=None):
    result=subprocess.run([str(x) for x in cmd],cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace')
    log.parent.mkdir(parents=True,exist_ok=True)
    log.write_text('$ '+' '.join(str(x) for x in cmd)+'\n'+result.stdout+result.stderr,encoding='utf-8')
    if result.returncode: raise BuildError(f'{log.name}: exit {result.returncode}; see {log}')
    return result.stdout+result.stderr

def expand(path, defines=None, once=None, stack=None):
    defines=set() if defines is None else defines; once=set() if once is None else once; stack=[] if stack is None else stack
    if path in stack: raise BuildError('Include cycle: '+str(path))
    stack=stack+[path]; result=[]; conditions=[]; active=True
    for line in read(path).splitlines():
        s=line.strip()
        m=re.match(r'!if(n?def)\s+(\w+)',s)
        if m:
            cond=(m[2] not in defines) if m[1]=='ndef' else (m[2] in defines)
            conditions.append((active,cond)); active=active and cond; continue
        if s=='!else':
            outer,cond=conditions[-1];active=outer and not cond; continue
        if s=='!endif':
            if conditions: active=conditions.pop()[0]
            continue
        if not active: continue
        m=re.match(r'!define\s+(\w+)',s)
        if m: defines.add(m[1]);continue
        m=re.match(r'!undef\s+(\w+)',s)
        if m: defines.discard(m[1]);continue
        m=re.match(r'!include(?:_(many|once))?\s+(.+)',s)
        if m:
            target=Path(os.path.abspath(path.parent/m[2].strip('"')))
            if not target.is_relative_to(ROOT): raise BuildError(f'Include outside repository: {path}: {s}')
            if not target.exists(): raise BuildError(f'Missing include: {path}: {s}')
            if target.name=='style.puml' or 'vendor' in target.parts: continue
            if m[1]!='many' and target in once: continue
            once.add(target); result.extend(expand(target,defines,once,stack));continue
        result.append(line)
    return result

ROLES={'teamlead','backend','analyst','techdoc','devops'}
MARKER='ci/integration-complete.json'

def previous_control_files(base_ref):
    if not base_ref or set(base_ref)=={'0'}:return {}
    if not re.fullmatch(r'[a-fA-F0-9]{40,64}',base_ref):raise BuildError('Base reference must be a commit SHA')
    r=subprocess.run(['git','ls-tree','-r','--name-only',base_ref,'--','ci/contributions',MARKER],cwd=ROOT,capture_output=True,text=True)
    if r.returncode:raise BuildError('Cannot read base commit; fetch full Git history')
    result={}
    for name in r.stdout.splitlines():
        p=subprocess.run(['git','show',base_ref+':'+name],cwd=ROOT,capture_output=True,text=True)
        if p.returncode:raise BuildError('Cannot read base control file '+name)
        result[name]=json.loads(p.stdout)
    return result

def phase(mode,base_ref):
    previous=previous_control_files(base_ref)
    for name,old in previous.items():
        p=ROOT/name
        if not p.is_file():raise BuildError('Cannot remove established integration/checkpoint marker: '+name)
        current=json.loads(read(p))
        if name!=MARKER and current['lab']<old['lab']:raise BuildError('Cannot roll back contribution stage: '+name)
    markers={}
    for p in (ROOT/'ci/contributions').glob('*.json'):
        data=json.loads(read(p));role=data.get('role')
        if role not in ROLES or p.stem!=role or role in markers:raise BuildError('Invalid contribution marker '+str(p))
        if data.get('lab') not in range(1,6):raise BuildError('Invalid laboratory stage '+str(p))
        for name in data['required_files']:
            path=ROOT/name
            if not path.is_relative_to(ROOT) or '..' in Path(name).parts or not path.is_file():raise BuildError('Missing required contribution file: '+name)
        markers[role]=data
    integrated=(ROOT/MARKER).is_file()
    scope='full' if mode=='full' or integrated else 'partial'
    if integrated:
        data=json.loads(read(ROOT/MARKER))
        if set(data['required_roles'])!=ROLES:raise BuildError('Invalid integration role list')
        if set(markers)!=ROLES or any(v['lab']!=5 for v in markers.values()):raise BuildError('Full integration requires five completed role contributions')
    return scope,markers

def lint(scope='full',markers=None):
    catalog=json.loads(read(ROOT/'ci/catalog.json'))
    entries=catalog['entries']; listed={e['source'] for e in entries}
    actual=set(); output_names=[]
    glossary=json.loads(read(ROOT/'ci/glossary.json'))
    source_files=list((ROOT/'src').rglob('*.puml'))+list((ROOT/'docs/flows').glob('*.puml'))
    for p in source_files:
        if 'vendor' in p.relative_to(ROOT).parts: continue
        text=read(p); rel=p.relative_to(ROOT).as_posix()
        if '@startuml' in text: actual.add(rel)
        if p.name!='style.puml':
            if re.search(r'^\s*(?:skinparam\b|AddElementTag\s*\(|Update\w*Style\s*\(|!\$[A-Z_]+\s*=)',text,re.M):
                raise BuildError('Local style is forbidden: '+rel)
        if re.search(r'\b(?:Rel_\w+|Lay_\w+|LAYOUT_\w+|RelIndex)\s*\(',text):
            raise BuildError('Directed relation or manual layout is forbidden: '+rel)
        for line in text.splitlines():
            m=re.match(r'\s*!include(?:_many|_once)?\s+(.+)',line)
            if m:
                target=Path(os.path.abspath(p.parent/m[1].strip('"')))
                if not target.is_relative_to(ROOT) or not target.exists():raise BuildError('Missing/unsafe include: '+rel+' -> '+m[1])
    if actual-listed or (scope=='full' and listed-actual):raise BuildError(f'Catalog mismatch: unlisted={actual-listed}; missing={listed-actual}')
    missing_sources=sorted(listed-actual)
    if scope=='partial':
        declared={'src/context/context.puml'}
        for data in (markers or {}).values():declared.update(data['required_files'])
        if actual-declared:raise BuildError('Model without contribution checkpoint: '+str(actual-declared))
        entries=[e for e in entries if e['source'] in actual]
    if not entries:raise BuildError('No architecture models found')
    models={}
    for e in entries:
        path=ROOT/e['source']; text=read(path)
        names=re.findall(r'^@startuml\s+([\w-]+)\s*$',text,re.M)
        if names!=e['outputs']: raise BuildError('Output names changed: '+e['source'])
        output_names.extend(names)
        # Each @startuml block is a separate namespace.
        expanded='\n'.join(expand(path)); all_defs=set();all_edges=set()
        for block in re.split(r'@startuml[^\n]*',expanded)[1:]:
            block=block.split('@enduml')[0]; defs=set();edges=set()
            for line in block.splitlines():
                m=re.match(r'\s*(Person(?:_Ext)?|System(?:Db|Queue)?(?:_Ext|_Boundary)?|Container(?:Db|Queue|_Boundary)?|Component(?:Db|Queue)?|Boundary|Deployment_Node|Node)\s*\(\s*(\w+)',line)
                if m:
                    if m[2] in defs:raise BuildError(f'Duplicate ID {m[2]}: {e["source"]}')
                    defs.add(m[2])
                    if m[1].startswith('Component') and m[2] not in glossary['components']:
                        raise BuildError('Unknown component ID: '+m[2])
                m=re.match(r'\s*Rel\((.*)\)\s*$',line)
                if m:
                    a=arguments(m[1])
                    if len(a)<4 or not a[2].strip() or not a[3].strip():raise BuildError('Rel needs action and protocol: '+line)
                    edges.add((a[0],a[1]))
            for a,b in edges:
                if a not in defs or b not in defs:raise BuildError(f'Unknown endpoint {a} -> {b}: {e["source"]}')
            all_defs.update(defs);all_edges.update(edges)
        models[e['source']]={'ids':sorted(all_defs),'edges':[list(x) for x in sorted(all_edges)]}
    if len(output_names)!=len(set(output_names)):raise BuildError('Duplicate output filename')
    for p in (ROOT/'ci/contracts').glob('*.json'):
        contract=json.loads(read(p))
        for check in contract['checks']:
            if check['source'] not in models and scope=='partial':continue
            model=models[check['source']]
            missing=set(check.get('required_ids',[]))-set(model['ids'])
            if missing:raise BuildError(f'{p.name}: missing IDs {missing}')
            edges={tuple(x) for x in model['edges']}
            for edge in check.get('required_edges',[]):
                if tuple(edge) not in edges:raise BuildError(f'{p.name}: missing edge {edge}')
            for edge in check.get('forbidden_edges',[]):
                if tuple(edge) in edges:raise BuildError(f'{p.name}: forbidden edge {edge}')
    return entries,models,missing_sources

def toolchain(jar):
    lock=json.loads(read(ROOT/'ci/toolchain.lock.json'))
    jar=Path(jar or ROOT/'.cache/plantuml.jar')
    if not jar.exists():
        jar.parent.mkdir(parents=True,exist_ok=True)
        temp=jar.with_suffix('.part')
        with urllib.request.urlopen(lock['plantuml']['url'],timeout=120) as response:temp.write_bytes(response.read())
        temp.replace(jar)
    if digest(jar)!=lock['plantuml']['sha256']:raise BuildError('PlantUML SHA-256 mismatch')
    for relative, expected in lock['vendor_sha256'].items():
        if digest(ROOT/relative)!=expected:raise BuildError('Vendored C4 changed: '+relative)
    return jar

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--lint-only',action='store_true');ap.add_argument('--jar');ap.add_argument('--skip-tests',action='store_true');ap.add_argument('--mode',choices=['full','auto'],default='full');ap.add_argument('--base-ref',default='');args=ap.parse_args()
    os.environ.setdefault('PLANTUML_LIMIT_SIZE','18000')
    work=ROOT/'.build';work.mkdir(exist_ok=True);logs=work/'logs'
    if logs.exists():shutil.rmtree(logs)
    logs.mkdir()
    published=work/'artifacts'; staging=work/'staging'
    # An invalid build must not expose old artifacts as fresh output.
    for p in [published,staging]:
        if p.exists():shutil.rmtree(p)
    summary={'status':'failed','started_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'hosted_actions_executed':os.environ.get('GITHUB_ACTIONS')=='true','plantuml_limit_size':os.environ['PLANTUML_LIMIT_SIZE']}
    try:
        scope,markers=phase(args.mode,args.base_ref)
        entries,models,missing=lint(scope,markers);summary.update({'scope':scope,'missing_sources':missing,'role_stages':{r:d['lab'] for r,d in markers.items()}})
        summary['source_count']=len(entries);summary['view_count']=sum(len(e['outputs']) for e in entries)
        (logs/'lint.json').write_text(json.dumps(models,ensure_ascii=False,indent=2),encoding='utf-8')
        if not args.lint_only:
            jar=toolchain(args.jar);java=os.environ.get('JAVA','java')
            versions=run([java,'-jar',jar,'-version'],logs/'toolchain.txt')
            summary['plantuml_version']=versions.splitlines()[0];summary['jar_sha256']=digest(jar)
            run([java,'-jar',jar,'-charset','UTF-8','-checkonly',*[ROOT/e['source'] for e in entries]],logs/'syntax.txt')
            staging.mkdir()
            for fmt in ['svg','png']:
                run([java,'-Djava.awt.headless=true','-jar',jar,'-charset','UTF-8','-failfast','-nbthread','2','-t'+fmt,'-o',staging,*[ROOT/e['source'] for e in entries]],logs/f'render-{fmt}.txt')
            for e in entries:
                for name in e['outputs']:
                    svg=staging/(name+'.svg');png=staging/(name+'.png')
                    if not svg.exists() or not png.exists():raise BuildError('Missing output '+name)
                    tree=ET.parse(svg)
                    if not tree.getroot().tag.endswith('svg'):raise BuildError('Invalid SVG '+name)
                    if 'Syntax Error?' in read(svg):raise BuildError('Rendered error page '+name)
                    data=png.read_bytes()
                    if data[:8]!=b'\x89PNG\r\n\x1a\n' or min(struct.unpack('>II',data[16:24]))<10:raise BuildError('Invalid PNG '+name)
            if scope=='partial':
                python_files=list((ROOT/'prototype').rglob('*.py'))
                for p in python_files:compile(read(p),str(p),'exec')
                summary['python_syntax_files']=len(python_files)
                summary['prototype_tests']='deferred until full integration'
            elif not args.skip_tests:
                env=os.environ.copy();env['PYTHONPATH']=str(ROOT/'prototype/src')+os.pathsep+env.get('PYTHONPATH','')
                result=run([sys.executable,'-m','unittest','discover','-s','prototype/tests','-v'],logs/'prototype-tests.txt',env)
                m=re.search(r'Ran (\d+) tests?',result);summary['prototype_tests']=int(m[1]) if m else None
            staging.rename(published)
            summary['artifact_files']=len(list(published.iterdir()))
        summary['status']='passed';summary['mode']=scope;summary['execution']='lint' if args.lint_only else 'render'
        print(json.dumps(summary,ensure_ascii=False,indent=2));return 0
    except Exception as exc:
        summary['error']=str(exc);(logs/'failure.txt').write_text(str(exc),encoding='utf-8');print(str(exc),file=sys.stderr);return 1
    finally:
        (work/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'],'a',encoding='utf-8') as f:f.write('scope='+summary.get('scope','failed')+'\n')
        if os.environ.get('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as f:
                f.write('## '+('Полная интеграция' if summary.get('scope')=='full' else 'Частичная проверка')+'\n\n'+json.dumps(summary,ensure_ascii=False,indent=2)+'\n')

if __name__=='__main__':sys.exit(main())
