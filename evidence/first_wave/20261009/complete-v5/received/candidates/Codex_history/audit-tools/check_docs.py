from pathlib import Path
import hashlib,importlib.util,sys
root=Path(__file__).resolve().parents[1]
path=root/'audit-tools/docs.py';data=path.read_bytes();assert hashlib.sha1(f'blob {len(data)}\0'.encode()+data).hexdigest()=='9de34e8187c718d21b5a5f8efcac878a42139b48'
spec=importlib.util.spec_from_file_location('history_docs_checker',path);module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
for name in ('codex-history.md','codex-history_ZH.md'):
 page=module.parse_page(root/'source/docs/reference'/name);other=module.parse_page(page.path.parent/page.metadata['counterpart']);assert page.metadata['name']==other.metadata['name'] and page.metadata['revision']==other.metadata['revision'];assert page.metadata['language']!=other.metadata['language'];assert other.metadata['counterpart']==name
 for token in page.tokens:
  for child in token.children or ():
   if child.type=='link_open':
    target=module.local_target(page.path,child.attrGet('href'),root/'source');assert target is None or target[0].is_file()
print('PASS: two added reference pages parse and bilingual metadata/local links agree. Full site build not run; isolated baseline omits site files.')
