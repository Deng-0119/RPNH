import json
import math
from pathlib import Path
import pytest
from cpn.rpnh import public_material_contracts as w

@pytest.mark.parametrize('value,expected',[(0.9,b'0.90000000000000002220446049250313080847263336181640625'),(0.73,b'0.729999999999999982236431605997495353221893310546875'),(-0.0,b'0.0'),(1.0,b'1.0'),(0.5,b'0.5'),('\n\t',b'"\\u000a\\u0009"'),({'b':1,'a':2},b'{"a":2,"b":1}')])
def test_public_c_goldens(value,expected):assert w.canonical(value)==expected

@pytest.mark.parametrize('value',[float('nan'),float('inf'),float('-inf'),b'bytes',{1:'bad'},object()])
def test_strict_python_boundary(value):
    with pytest.raises((ValueError,TypeError)):w.canonical(value)

@pytest.mark.parametrize('raw',[b'{"a":1,"a":2}',b'NaN',b'Infinity',b'1e999',b'"\\ud800"',b'\xef\xbb\xbf{}'])
def test_strict_json_boundary(raw):
    with pytest.raises((ValueError,TypeError,UnicodeError)):w.decode(raw)

@pytest.mark.parametrize('value',[float(5e-324),float(1.7976931348623157e308),-1.25,math.pi])
def test_float_full_domain_roundtrip(value):
    raw=w.canonical(value);assert b'e' not in raw
    assert w.decode(raw,canonical_required=True)==value


def test_large_integer_without_global_digit_limit_change():
    import sys
    limit=sys.get_int_max_str_digits()
    raw=b'1'+b'0'*5000
    assert w.canonical(w.decode(raw,canonical_required=True))==raw
    assert sys.get_int_max_str_digits()==limit


def test_cycles_subclasses_and_unicode_scalars():
    value=[];value.append(value)
    class Float(float):pass
    for v in (value,Float(1.2),'\ud800'):
        with pytest.raises((ValueError,TypeError,UnicodeError)):w.canonical(v)


def test_original_protocol_is_not_reencoded_as_public_c():
    from cpn.rpnh.registry.parent_child import _json
    assert _json({'ratio':0.73})==b'{"ratio":0.73}'
    assert w.canonical({'ratio':0.73})!=_json({'ratio':0.73})


def test_design_goldens_independently_match_new_product_encoder():
    examples=Path(__file__).resolve().parent/'fixtures/public_material_contract_v3'
    for name in ('D_INPUT','PUBLIC_POLICY'):
        value=w.decode((examples/(name+'.json')).read_bytes())
        assert w.canonical(value)==(examples/(name+'.canonical.json')).read_bytes()
    policy=w.decode((examples/'PUBLIC_POLICY.json').read_bytes())
    assert w.validate_policy(policy)==policy
    policy['route_provenance'][0]['outbound_model']='different'
    with pytest.raises(ValueError,match='exact model'):w.validate_policy(policy)
