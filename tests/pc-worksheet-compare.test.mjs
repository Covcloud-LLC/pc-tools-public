import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

test('worksheet comparison and report: adopted cases, validation, uncertainty and protected publication', () => {
  const dir = fileURLToPath(new URL('./worksheet-compare/', import.meta.url));
  const run = spawnSync(process.env.PYTHON || 'python3', ['-m', 'unittest', 'discover', '-s', dir, '-p', 'test_*.py', '-v'], { encoding: 'utf8' });
  assert.ifError(run.error);
  assert.equal(run.status, 0, run.stdout + run.stderr);
  assert.match(run.stderr, /^Ran (\d+) tests?/m);
  assert.ok(Number(run.stderr.match(/^Ran (\d+) tests?/m)[1]) >= 29, run.stderr);
});
