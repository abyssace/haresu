// Interface smoke test: starts a test backend (with fake providers on ports
// 8766 and 8767, which the tests themselves open) and runs the frontend/ tests against it.
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const win = process.platform === 'win32';
const venvPython = join(root, 'backend', '.venv', win ? 'Scripts/python.exe' : 'bin/python');
const python = process.env.HARESU_PYTHON || (existsSync(venvPython) ? venvPython : win ? 'python' : 'python3');
const port = 8765;
const home = mkdtempSync(join(tmpdir(), 'haresu-ui-'));

const env = {
  ...process.env,
  HARESU_HOME: home,
  HARESU_OPENAI_BASE_URL: 'http://127.0.0.1:8766/v1',
  HARESU_OPENROUTER_BASE_URL: 'http://127.0.0.1:8766/or/v1',
  HARESU_OPENCODE_BASE_URL: 'http://127.0.0.1:8767/oc',
};
for (const k of ['ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'OPENAI_API_KEY', 'OPENROUTER_API_KEY', 'OPENCODE_API_KEY',
  'GEMINI_API_KEY', 'GOOGLE_API_KEY', 'GROQ_API_KEY', 'MISTRAL_API_KEY', 'DEEPSEEK_API_KEY']) delete env[k];

const server = spawn(python, ['-m', 'haresu', 'serve', '--port', String(port)], { cwd: join(root, 'backend'), env, stdio: 'ignore' });
const stop = () => { server.kill(); rmSync(home, { recursive: true, force: true }); };
process.on('SIGINT', () => { stop(); process.exit(130); });

let up = false;
for (let i = 0; i < 60 && !up; i++) {
  try { up = (await fetch(`http://127.0.0.1:${port}/api/catalog`)).ok; } catch { await new Promise((r) => setTimeout(r, 500)); }
}
if (!up) { stop(); console.error('The test backend did not start.'); process.exit(1); }

const r = spawnSync('npx', ['vitest', 'run'], {
  cwd: join(root, 'frontend'), stdio: 'inherit', shell: win, env: { ...process.env, HARESU_URL: `http://127.0.0.1:${port}` },
});
stop();
process.exit(r.status ?? 1);
