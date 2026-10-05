import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

test('worksheet extraction: synthetic Python regression suite', () => {
  const file = fileURLToPath(new URL('./worksheet/test_extract.py', import.meta.url));
  const run = spawnSync(process.env.PYTHON || 'python3', [file, '-v'], { encoding: 'utf8' });
  assert.ifError(run.error);
  assert.equal(run.status, 0, run.stdout + run.stderr);
});
