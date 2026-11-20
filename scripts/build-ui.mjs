// Compiles the interface (frontend/) and copies it into the Python package (backend/haresu/static),
// which is what gets installed with npm or pip. Run it whenever the frontend/ code changes.
import { cpSync, existsSync, rmSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const shell = process.platform === 'win32';
const run = (args) => {
  const r = spawnSync('npm', ['--prefix', join(root, 'frontend'), ...args], { stdio: 'inherit', shell });
  if (r.status !== 0) process.exit(r.status ?? 1);
};

if (!existsSync(join(root, 'frontend', 'node_modules'))) run(['install']);
run(['run', 'build']);
const target = join(root, 'backend', 'haresu', 'static');
rmSync(target, { recursive: true, force: true });
cpSync(join(root, 'frontend', 'dist'), target, { recursive: true });
console.log(`Interface copied to ${target}`);
