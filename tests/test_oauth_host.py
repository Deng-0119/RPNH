import os
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import rpnh_gh_interactive_oauth_host as host
from cpn.plugins.account_credentials import AccountSelection, AccountCallScope, AccountCredentialError, resolve_account_fd
from cpn.plugins.github_oauth_provider import GitHubOAuthAccount, GitHubOAuthAccountProvider
from cpn.plugins.account_bindings import AccountBindingRequest
from cpn.rpnh.collaboration.references import SourceQualifiedVersionRef, SourceQualifiedResourceRef
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.resources import ResourceVersionRef

def ref(kind):
    return SourceQualifiedVersionRef('local',VersionRef(kind+'/v1',TypedId(kind,'a'*32),TypedId(kind+'_version','b'*32)))

def fixture():
    s=AccountSelection(ref('principal'),ref('task'),SourceQualifiedResourceRef('local',ResourceVersionRef(TypedId('resource','c'*32),TypedId('resource_version','d'*32))),'github','Alice','r1',slot='github_oauth/v1')
    c=AccountCallScope(s.principal_ref,s.task_ref,ref('operation_execution_lease'),'tool-key','call-1')
    a=GitHubOAuthAccount('github.com','github','Alice')
    source=host.InteractiveGitHubSource()
    resolver=GitHubOAuthAccountProvider(AccountBindingRequest('dsh','read','github','Alice','r1',slot='github_oauth/v1'),host='github.com',current_use_check=source.current_use,credential_source=source.credential).bind(s)
    return s,c,a,source,resolver

class Tests(unittest.TestCase):
    def test_original_resolver_delivers_once(self):
        s,c,a,source,r=fixture()
        with patch.object(host,'_confirm',return_value=True),patch.object(host,'_gh_token',return_value=b'offline_fixture'),patch.object(host,'_identity',return_value='alice'):
            with resolve_account_fd(s,c,r) as result:
                fd=result.take_fd()
                self.assertFalse(os.get_inheritable(fd));self.assertEqual(os.read(fd,4096),b'offline_fixture');os.close(fd)
            with self.assertRaises(AccountCredentialError):source.credential(a,s,c)
    def test_deny_does_not_fetch(self):
        s,c,a,source,r=fixture()
        with patch.object(host,'_confirm',return_value=False),patch.object(host,'_gh_token') as token:
            with self.assertRaises(AccountCredentialError):
                with resolve_account_fd(s,c,r):pass
            token.assert_not_called()
    def test_truthy_not_consent(self):
        s,c,a,source,r=fixture()
        with patch.object(host,'_confirm',return_value='yes'),patch.object(host,'_gh_token') as token:
            with self.assertRaises(AccountCredentialError):
                with resolve_account_fd(s,c,r):pass
            token.assert_not_called()
    def test_identity_mismatch(self):
        s,c,a,source,r=fixture()
        with patch.object(host,'_confirm',return_value=True),patch.object(host,'_gh_token',return_value=b'fixture'),patch.object(host,'_identity',return_value='Bob'):
            with self.assertRaises(AccountCredentialError):
                with resolve_account_fd(s,c,r):pass
    def test_no_terminal_denies(self):
        s,c,a,source,r=fixture()
        with patch('builtins.open',side_effect=OSError('no tty')):
            self.assertFalse(source.current_use(a,s,c))
    def test_scope_change_denies(self):
        s,c,a,source,r=fixture()
        with patch.object(host,'_confirm',return_value=True):self.assertTrue(source.current_use(a,s,c))
        other=AccountCallScope(c.principal_ref,c.task_ref,c.execution_ref,c.registration_key,'call-2')
        with patch.object(host,'_gh_token') as token:
            with self.assertRaises(AccountCredentialError):source.credential(a,s,other)
            token.assert_not_called()
    def test_source_failure_consumes_consent(self):
        s,c,a,source,r=fixture()
        with patch.object(host,'_confirm',return_value=True):source.current_use(a,s,c)
        with patch.object(host,'_gh_token',side_effect=AccountCredentialError()):
            with self.assertRaises(AccountCredentialError):source.credential(a,s,c)
        with patch.object(host,'_gh_token') as token:
            with self.assertRaises(AccountCredentialError):source.credential(a,s,c)
            token.assert_not_called()
    def test_ambient_token_removed_explicit_account(self):
        s,c,a,source,r=fixture()
        with patch.dict(os.environ,{'RPNH_GH_EXECUTABLE':'/usr/bin/gh','GH_TOKEN':'secret','GITHUB_TOKEN':'secret','GH_DEBUG':'api'}),patch.object(host.os.path,'isfile',return_value=True),patch.object(host.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=b'gho_fixture\n')) as run:
            self.assertEqual(host._gh_token(a),b'gho_fixture')
            args,kw=run.call_args
            self.assertEqual(args[0][-2:],['--user','Alice'])
            self.assertNotIn('GH_TOKEN',kw['env']);self.assertNotIn('GITHUB_TOKEN',kw['env']);self.assertNotIn('GH_DEBUG',kw['env'])
            self.assertEqual(kw['stderr'],host.subprocess.DEVNULL)
    def test_pat_gh_token_not_oauth(self):
        s,c,a,source,r=fixture()
        with patch.dict(os.environ,{'RPNH_GH_EXECUTABLE':'/usr/bin/gh'}),patch.object(host.os.path,'isfile',return_value=True),patch.object(host.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=b'ghp_fixture')):
            with self.assertRaises(AccountCredentialError):host._gh_token(a)
    def test_no_absolute_gh_denies(self):
        s,c,a,source,r=fixture()
        with patch.dict(os.environ,{'RPNH_GH_EXECUTABLE':'gh'}),self.assertRaises(AccountCredentialError):host._gh_token(a)
    def test_secret_failure_sanitized(self):
        s,c,a,source,r=fixture()
        with patch.dict(os.environ,{'RPNH_GH_EXECUTABLE':'/usr/bin/gh'}),patch.object(host.os.path,'isfile',return_value=True),patch.object(host.subprocess,'run',side_effect=host.subprocess.TimeoutExpired('secret',30)):
            with self.assertRaises(AccountCredentialError) as ctx:host._gh_token(a)
            self.assertNotIn('secret',str(ctx.exception))
    def test_factory_rejects_write(self):
        s,c,a,source,r=fixture()
        catalog=SimpleNamespace(declaration=lambda n:None,binding=lambda n:(None,SimpleNamespace(effect='external_write')))
        with self.assertRaises(AccountCredentialError):host.factory(owner=None,catalog=catalog,node_id='dsh',account_selections={'read':s})
    def test_factory_original_resolver(self):
        s,c,a,source,r=fixture()
        catalog=SimpleNamespace(declaration=lambda n:None,binding=lambda n:(None,SimpleNamespace(effect='external_read')))
        result=host.factory(owner=None,catalog=catalog,node_id='dsh',account_selections={'read':s})
        self.assertEqual(result['read'].selection,s)



class ContractTests(unittest.TestCase):
    def test_typed_slots_cannot_cross(self):
        from cpn.plugins.github_pat_provider import GitHubPATAccountProvider
        s,c,a,source,r=fixture()
        with self.assertRaises(TypeError):
            GitHubPATAccountProvider(AccountBindingRequest('dsh','read','github','Alice','r1',slot='github_oauth/v1'),host='github.com',current_use_check=None,credential_source=None)
        with self.assertRaises(TypeError):
            GitHubOAuthAccountProvider(AccountBindingRequest('dsh','read','github','Alice','r1'),host='github.com',current_use_check=None,credential_source=None)
    def test_original_renderer_with_oauth_lease(self):
        from cpn.plugins.account_credentials import resolved_stdio_environment
        s,c,a,source,r=fixture()
        with patch.object(host,'_confirm',return_value=True),patch.object(host,'_gh_token',return_value=b'gho_OFFLINE_FIXTURE'),patch.object(host,'_identity',return_value='alice'):
            with resolve_account_fd(s,c,r) as lease:
                with resolved_stdio_environment(s,c,lease,base_environment={}) as env:
                    self.assertEqual(env['GITHUB_PERSONAL_ACCESS_TOKEN'],'gho_OFFLINE_FIXTURE')
                self.assertEqual(env,{})
    def test_original_declaration_accepts_typed_requirement(self):
        from cpn.plugins.api import checked_account_requirement
        self.assertEqual(checked_account_requirement({'service_id':'github','slot':'github_oauth/v1'})['slot'],'github_oauth/v1')

    def test_production_schema_roundtrips(self):
        from cpn.plugins.account_bindings import AccountBindingPlan,account_binding_schema_data,account_publication_config,BINDING_SCHEMA,PUBLICATION_CONFIG_SCHEMA
        from jsonschema import Draft7Validator
        s,c,a,source,r=fixture()
        request=AccountBindingRequest('dsh','read','github','Alice','r1',slot='github_oauth/v1')
        plan=AccountBindingPlan('local',(request,))
        self.assertEqual(AccountBindingPlan.from_dict(plan.to_dict()),plan)
        schemas,_,_=account_binding_schema_data()
        Draft7Validator(schemas[BINDING_SCHEMA]).validate(s.binding_document())
        Draft7Validator(schemas[PUBLICATION_CONFIG_SCHEMA]).validate(account_publication_config(plan))

if __name__=='__main__':unittest.main(verbosity=2)
