#!/usr/bin/env node
'use strict';
// haresu launcher for npm. The app is Python (backend) with the interface already compiled inside the package:
// the first time it creates a Python environment in ~/.haresu/runtime and installs the bundled package in it; afterwards it only
// runs `python -m haresu ...`. Everything this file writes goes to stderr, so it does not pollute the channel
// of `haresu mcp`, which talks over stdout.

const { spawn, spawnSync } = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const backend = path.join(root, 'backend');
const version = require(path.join(root, 'package.json')).version;
const runtime = path.join(os.homedir(), '.haresu', 'runtime');
const venv = path.join(runtime, 'venv');
const win = process.platform === 'win32';
const venvPython = win ? path.join(venv, 'Scripts', 'python.exe') : path.join(venv, 'bin', 'python');
const stampFile = path.join(runtime, 'installed.json');
const log = (msg) => process.stderr.write(`haresu: ${msg}\n`);

function fail(msg) {
  log(msg);
  process.exit(1);
}

function run(cmd, args, opts = {}) {
  return spawnSync(cmd, args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], shell: false, ...opts });
}

// A fingerprint of the bundled code: if it changes (another version, another commit), it is reinstalled.
function fingerprint() {
  const hash = crypto.createHash('sha256');
  hash.update(version);
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      if (entry.name === '__pycache__' || entry.name.endsWith('.pyc')) continue;
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else { hash.update(path.relative(backend, full)); hash.update(fs.readFileSync(full)); }
    }
  };
  walk(path.join(backend, 'haresu'));
  hash.update(fs.readFileSync(path.join(backend, 'pyproject.toml')));
  return hash.digest('hex').slice(0, 16);
}

// A Python 3.10 or newer.
function findPython() {
  const candidates = process.env.HARESU_PYTHON ? [[process.env.HARESU_PYTHON]]
    : win ? [['py', '-3'], ['python'], ['python3']]
      : [['python3.13'], ['python3.12'], ['python3.11'], ['python3.10'], ['python3'], ['python']];
  for (const [cmd, ...pre] of candidates) {
    const r = run(cmd, [...pre, '-c', 'import sys; print(sys.version_info >= (3, 10))']);
    if (!r.error && r.status === 0 && r.stdout.trim() === 'True') return [cmd, ...pre];
  }
  return null;
}

function ensureRuntime() {
  const want = fingerprint();
  let have = null;
  try { have = JSON.parse(fs.readFileSync(stampFile, 'utf8')).fingerprint; } catch { /* primera vez */ }
  if (have === want && fs.existsSync(venvPython)) return;

  const python = findPython();
  const uv = run('uv', ['--version']);
  if (!python && uv.status !== 0) {
    fail('I need Python 3.10 or newer and I did not find it. Install it (https://www.python.org/downloads/), '
      + 'install uv (https://docs.astral.sh/uv/), which gets one on its own, or point to one with HARESU_PYTHON=/path/to/python3.');
  }
  log(`preparing haresu ${version} (first time only, it can take a minute or two)…`);
  fs.mkdirSync(runtime, { recursive: true });
  if (!fs.existsSync(venvPython)) {
    // Without a suitable Python on the system, uv finds or downloads one.
    const made = python
      ? run(python[0], [...python.slice(1), '-m', 'venv', venv])
      : run('uv', ['venv', '--python', '>=3.10', venv]);
    if (made.status !== 0) fail(`could not create the Python environment:\n${made.stderr || made.error}`);
  }
  const install = uv.status === 0
    ? run('uv', ['pip', 'install', '--quiet', '--python', venvPython, backend])
    : run(venvPython, ['-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', backend]);
  if (install.status !== 0) fail(`could not install the Python dependencies:\n${install.stderr || install.error}`);
  fs.writeFileSync(stampFile, JSON.stringify({ version, fingerprint: want }));
  log('done.');
}

const args = process.argv.slice(2);
if (args[0] === '--version' || args[0] === '-v') {
  process.stdout.write(`${version}\n`);
  process.exit(0);
}
ensureRuntime();
const forwarded = args.length === 0 ? ['serve', '--open'] : args; // with no arguments: start the interface and open it
const child = spawn(venvPython, ['-m', 'haresu', ...forwarded], { stdio: 'inherit' });
for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => child.kill(sig));
child.on('exit', (code, signal) => process.exit(signal ? 1 : code ?? 0));
child.on('error', (err) => fail(`could not start haresu: ${err.message}`));
