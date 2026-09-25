#!/usr/bin/env python3
"""Validate catalog examples against the actual source schema; no RPNH imports."""
from pathlib import Path
import argparse
import copy
import json
from jsonschema import Draft7Validator, ValidationError
from markdown_it import MarkdownIt


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--schema', type=Path,
        default=root / 'cpn/schemas/runtime/provider_model_catalog.v2.schema.json')
    args = parser.parse_args()
    schema = json.loads(args.schema.read_text(encoding='utf-8'))
    Draft7Validator.check_schema(schema)
    validator = Draft7Validator(schema)
    examples = []
    for path in sorted((root / 'docs/guides').glob('models*.md')):
        for token in MarkdownIt().parse(path.read_text(encoding='utf-8')):
            if token.type == 'fence' and token.info == 'json':
                example = json.loads(token.content)
                validator.validate(example)
                examples.append(example)
    if len(examples) != 2 or examples[0] != examples[1]:
        raise ValueError('Expected two equal bilingual catalog examples')
    validator.validate({'schema_version': 'rpnh/provider_model_catalog/v2', 'providers': []})
    for mutation in ('protocol', 'recovery_limit', 'unknown_key'):
        bad = copy.deepcopy(examples[0])
        model = bad['providers'][0]['models'][0]
        if mutation == 'protocol':
            model['adapter']['protocol'] = 'invented/v1'
        elif mutation == 'recovery_limit':
            model['adapter']['recovery']['max_probe_attempts'] = 4
        else:
            model['unexpected'] = 1
        try:
            validator.validate(bad)
        except ValidationError:
            continue
        raise ValueError('Schema accepted invalid mutation: ' + mutation)
    print(json.dumps({'bilingual_examples_valid': 2, 'empty_catalog_valid': True,
                      'invalid_mutations_rejected': 3,
                      'scope': 'schema only; not generator or live transport validation'}, indent=2))


if __name__ == '__main__':
    main()
