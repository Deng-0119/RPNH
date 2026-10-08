#!/usr/bin/env python3
"""Search Python, JavaScript, and C++ source files with exact, regex and structural pattern rules."""
import argparse, json, re, sys
from pathlib import Path

LANGUAGE_BY_EXTENSION = {'.py':'python','.js':'javascript','.mjs':'javascript','.cjs':'javascript','.cc':'cpp','.cpp':'cpp','.cxx':'cpp','.hh':'cpp','.hpp':'cpp','.hxx':'cpp'}
ALL_LANGUAGES = frozenset(('python','javascript','cpp'))
META_RE = re.compile(r'^\$[A-Za-z_][A-Za-z0-9_]*\??$')

def fail(message):
    print(message, file=sys.stderr); return 2

def lex(s, language):
    """Return significant tokens as (value, start, end); comments and white space vanish."""
    out=[]; i=0; n=len(s)
    while i<n:
        c=s[i]
        if c.isspace(): i+=1; continue
        if s.startswith('//',i) and language != 'python':
            j=s.find('\n',i); i=n if j<0 else j; continue
        if c=='#' and language=='python':
            j=s.find('\n',i); i=n if j<0 else j; continue
        if s.startswith('/*',i) and language != 'python':
            j=s.find('*/',i+2); i=n if j<0 else j+2; continue
        if c in "'\"":
            q=c; triple=language=='python' and s.startswith(q*3,i); k=i+(3 if triple else 1)
            while k<n:
                if s[k]=='\\': k+=2; continue
                if triple and s.startswith(q*3,k): k+=3; break
                if not triple and s[k]==q: k+=1; break
                k+=1
            out.append((s[i:k],i,k)); i=k; continue
        m=re.match(r'[A-Za-z_$][A-Za-z0-9_$]*|(?:\d+(?:\.\d*)?|\.\d+)',s[i:])
        if m:
            k=i+len(m.group()); out.append((s[i:k],i,k)); i=k; continue
        # Treat common multi-character operators as one structural token.
        got=False
        for op in ('===','!==','>>>','**=','=>','==','!=','<=','>=','&&','||','++','--','+=','-=','*=','/=','%=','->','::','**','<<','>>','??','?.'):
            if s.startswith(op,i): out.append((op,i,i+len(op))); i+=len(op); got=True; break
        if not got: out.append((c,i,i+1)); i+=1
    return out

def parse_rules(path):
    try:
        with open(path, encoding='utf-8') as f: rules=json.load(f)
    except (OSError,UnicodeDecodeError,json.JSONDecodeError) as e: raise ValueError(f'unable to read rules: {e}')
    if not isinstance(rules,list): raise ValueError('rules file must contain a JSON array')
    seen=set(); ans=[]; flag_values={'i':re.I,'m':re.M,'s':re.S}
    for r in rules:
        if not isinstance(r,dict): raise ValueError('each rule must be an object')
        rid,kind,pat=r.get('id'),r.get('kind'),r.get('pattern')
        if not isinstance(rid,str) or not rid: raise ValueError('rule id must be a non-empty string')
        if rid in seen: raise ValueError('rule ids must be unique')
        seen.add(rid)
        if kind not in ('exact','regex','pattern'): raise ValueError('rule kind must be exact, regex or pattern')
        if not isinstance(pat,str) or not pat: raise ValueError('rule pattern must be a non-empty string')
        langs=r.get('languages',list(ALL_LANGUAGES))
        if not isinstance(langs,list) or any(not isinstance(x,str) or x not in ALL_LANGUAGES for x in langs): raise ValueError('languages may only contain python, javascript, or cpp strings')
        flags=r.get('regex_flags',[])
        if kind=='regex':
            if not isinstance(flags,list) or any(x not in flag_values for x in flags): raise ValueError('invalid regex_flags')
            try: matcher=re.compile(pat, sum((flag_values[x] for x in flags),0))
            except re.error as e: raise ValueError(f'invalid regex for {rid}: {e}')
        else:
            if 'regex_flags' in r: raise ValueError('regex_flags is only allowed for regex rules')
            matcher=None
        ans.append((rid,kind,pat,matcher,frozenset(langs)))
    return ans

def position(text,index):
    return {'line':text.count('\n',0,index)+1,'col':index-text.rfind('\n',0,index)}

def find_exact(text,pat):
    start=0
    while True:
        i=text.find(pat,start)
        if i<0:return
        yield i,i+len(pat),pat; start=i+len(pat)

def source_files(root):
    return sorted(((p,LANGUAGE_BY_EXTENSION[p.suffix]) for p in root.rglob('*') if p.is_file() and p.suffix in LANGUAGE_BY_EXTENSION),key=lambda x:x[0].relative_to(root).as_posix())

def balanced(tokens,a,b):
    # A metavariable represents one syntactic element, so it cannot eat an unmatched delimiter.
    stack=[]; pairs={')':'(',']':'[','}':'{'}
    for val,_,_ in tokens[a:b]:
        if val in '([{': stack.append(val)
        elif val in ')]}':
            if not stack or stack.pop()!=pairs[val]: return False
    return not stack

def pattern_tokens(pattern, language):
    ts=lex(pattern,language); out=[]; i=0
    while i<len(ts):
        v=ts[i][0]
        if v=='$' and i+1<len(ts) and ts[i+1][0]=='$': out.append(('LIT','$')); i+=2
        elif META_RE.match(v): out.append(('META',v)); i+=1
        else: out.append(('LIT',v)); i+=1
    return out

def find_patterns(text, pattern, language):
    src=lex(text,language); pat=pattern_tokens(pattern,language)
    if not pat:return
    # recursion tries shorter captures first, yielding deterministic innermost/end-first matches
    def rec(pi, si, caps):
        if pi==len(pat): yield si,caps; return
        typ,val=pat[pi]
        if typ=='LIT':
            if si<len(src) and src[si][0]==val: yield from rec(pi+1,si+1,caps)
            return
        optional=val.endswith('?'); name=val[:-1] if optional else val
        if optional: yield from rec(pi+1,si,caps)
        # one element extends to any balanced token boundary; next literals/captures constrain it
        for end in range(si+1,len(src)+1):
            if not balanced(src,si,end): continue
            raw=text[src[si][1]:src[end-1][2]]
            if name in caps and caps[name][0]!=raw: continue
            nc=dict(caps)
            if name in nc: nc[name]=(nc[name][0],nc[name][1]+[(src[si][1],src[end-1][2])])
            else: nc[name]=(raw,[(src[si][1],src[end-1][2])])
            yield from rec(pi+1,end,nc)
    seen=set()
    for first in range(len(src)):
        for end,caps in rec(0,first,{}):
            if end<=first: continue
            key=(src[first][1],src[end-1][2],tuple((k,v[0],tuple(v[1])) for k,v in sorted(caps.items())))
            if key in seen: continue
            seen.add(key)
            captures={}
            for name,(raw,ranges) in sorted(caps.items()):
                captures[name]={'text':raw,'ranges':[{'start':position(text,a),'end':position(text,b)} for a,b in ranges]}
            yield src[first][1],src[end-1][2],text[src[first][1]:src[end-1][2]],captures

def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('root_dir'); ap.add_argument('--rules',required=True); ap.add_argument('--encoding',default='utf-8'); args=ap.parse_args(argv)
    try: rules=parse_rules(args.rules)
    except ValueError as e:return fail(str(e))
    root=Path(args.root_dir)
    if not root.is_dir(): return fail(f'root_dir is not a directory: {root}')
    results=[]
    try: files=source_files(root)
    except OSError as e:return fail(str(e))
    for path,lang in files:
        try:text=path.read_text(encoding=args.encoding)
        except (OSError,UnicodeError,LookupError):continue
        rel=path.relative_to(root).as_posix()
        for rid,kind,pat,matcher,langs in rules:
            if lang not in langs:continue
            if kind=='exact': matches=((a,b,m,None) for a,b,m in find_exact(text,pat))
            elif kind=='regex': matches=((m.start(),m.end(),m.group(0),None) for m in matcher.finditer(text))
            else: matches=find_patterns(text,pat,lang)
            for a,b,m,caps in matches:
                item={'rule_id':rid,'file':rel,'language':lang,'start':position(text,a),'end':position(text,b),'match':m}
                if caps is not None:item['captures']=caps
                results.append(item)
    results.sort(key=lambda x:(x['file'],x['start']['line'],x['start']['col'],x['end']['line'],x['end']['col'],x['rule_id']))
    for x in results: print(json.dumps(x,ensure_ascii=False,separators=(',',':')))
    return 0
if __name__=='__main__': sys.exit(main())
