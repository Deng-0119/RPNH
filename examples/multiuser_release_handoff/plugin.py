"""Native managed tools for the local release world; no writer in workers."""
import json
import socket
import struct

from cpn.plugins.api import PluginDefinition, PluginOperation


def send(connection, value):
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
    if len(raw) > 1024 * 1024:
        raise ValueError('release transport frame exceeds one MiB')
    connection.sendall(struct.pack('!I', len(raw)) + raw)


def receive(connection, check=lambda: None):
    def read(size):
        data = bytearray()
        while len(data) < size:
            check()
            try:
                chunk = connection.recv(size-len(data))
            except socket.timeout:
                continue
            if not chunk:
                raise EOFError('release transport closed')
            data.extend(chunk)
        return bytes(data)
    size = struct.unpack('!I', read(4))[0]
    if not 0 < size <= 1024 * 1024:
        raise ValueError('invalid release frame size')
    return json.loads(read(size))


def invoke(context, arguments, tool):
    context.check_cancelled()
    identity = {key: getattr(context, key) for key in
                ('operation_id', 'invocation_id', 'firing_id', 'call_id')}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.1)
        connection.connect(context.config['endpoint'])
        send(connection, {'identity':identity, 'tool':tool, 'arguments':arguments})
        reply = receive(connection, context.check_cancelled)
        if reply.get('identity') != identity:
            raise RuntimeError('release effect unknown; reconcile original invocation')
        if reply['observation'].get('effect') == 'requires_reconciliation':
            raise RuntimeError('release effect unknown; original HOST evidence retained')
        return reply['observation']


def discover(c, a): return invoke(c, a, 'discover')
def read_note(c, a): return invoke(c, a, 'read_note')
def finish_read(c, a): return invoke(c, a, 'finish_read')
def read_git_file(c, a): return invoke(c, a, 'read_git_file')
def handoff(c, a): return invoke(c, a, 'handoff')
def await_handoff(c, a): return invoke(c, a, 'await_handoff')
def status(c, a): return invoke(c, a, 'status')
def create_file(c, a): return invoke(c, a, 'create_file')
def update_note(c, a): return invoke(c, a, 'update_note')
def create_branch(c, a): return invoke(c, a, 'create_branch')
def write_sql(c, a): return invoke(c, a, 'write_sql')
def write_checklist(c, a): return invoke(c, a, 'write_checklist')
def create_incident(c, a): return invoke(c, a, 'create_incident')
def confirm_release(c, a): return invoke(c, a, 'confirm_release')


COMMON = ('discover','read_note','finish_read','read_git_file','handoff','await_handoff','status')
ROLE_TOOLS = {'operations':COMMON+('create_file','update_note'),
    'developer':COMMON+('create_branch','write_sql'),
    'qa':COMMON+('write_checklist','confirm_release'),
    'support':COMMON+('create_incident','confirm_release')}
DESCRIPTIONS = {
    'discover':'Read a published project index through your authorized Registry link. path starts at project; follow returned relative child links.',
    'read_note':'Bind and read the current canonical mutable release note using path from discover. Holds a native source PN read claim until finish_read. Save its receipt/version.',
    'finish_read':'Release your exact read claim after using the body; provide read_id and a truthful findings summary. This does not certify a later version.',
    'read_git_file':'Read an actual Git file at branch_name and filePath, optionally exact commit; owner/repo are platform_admin/mini-seed-repo.',
    'handoff':'Publish a source-bearing message for another controlled role. recipient is operations/developer/qa/support; topic and text are required.',
    'await_handoff':'Wait for a published message to your role, optionally filtered by topic, for up to 45 seconds. Returns pending without inventing progress.',
    'status':'Read current release metadata and version readiness without exposing colleague private prompts/logs.',
    'create_file':'Create local Drive shell with exact original name,mimeType,parents,description, and your release/rollback body. Returns stable canonical identity.',
    'update_note':'Edit SAME canonical note. Pass expected_version and body with your business reason. Returns busy while native readers hold it; retry only confirmed no-effect busy.',
    'create_branch':'Create branch in real local Git. Parameters owner,repo,branch,old_branch.',
    'write_sql':'Commit SQL file sql/patches/sql_export_guard.sql with original content/message using owner,repo,branch_name,filePath,content,message.',
    'write_checklist':'Commit QA checklist qa/checklists/sql-export-guard_qa_checklist.md with original template/message; final evidence is separately confirmed.',
    'create_incident':'Create local support incident with the original exact fields; attach validation_evidence with actual note version/SQL commit if available.',
    'confirm_release':'Confirm current note after actual reread. Requires read_id, note_version, sql_commit, final_verification_time, findings; support also incident_id. Stale confirmation is rejected.'}
WRITE = {'create_file','update_note','create_branch','write_sql','write_checklist','create_incident','handoff','finish_read','confirm_release','read_note'}


def factory():
    return PluginDefinition(name='release_handoff',version='0.1.0',
        config_schema={'type':'object','additionalProperties':False,
            'properties':{'endpoint':{'type':'string'}},'required':['endpoint']},
        operations=tuple(PluginOperation(name=name,description=description,
            input_schema={'type':'object'},output_schema={'type':'object'},handler=globals()[name],
            effect='external_write' if name in WRITE else 'external_read',timeout_seconds=180)
            for name,description in DESCRIPTIONS.items()))


def configuration(endpoint):
    return {'schema_version':'rpnh/plugins/v1','plugins':[{'name':'release_handoff',
        'entry_point':'release_handoff','version':'0.1.0','config':{'endpoint':str(endpoint)},'environment':[]}]}
