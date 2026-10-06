import fs from 'node:fs';
import * as api from '../cpn/frontend/static/worksets.mjs';
import {runNormalChildRootViewFixture} from './normal_child_root_view.fixture.mjs';
import {exerciseConsumers} from './normal_child_consumer_cases.mjs';
const views = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
console.log(JSON.stringify({node:process.version, product_import: new URL('../cpn/frontend/static/worksets.mjs', import.meta.url).href,
    genuine_registry_js:exerciseConsumers(api, views.v1, views.v2),
    separately_mocked_dom:runNormalChildRootViewFixture()}, null, 2));
