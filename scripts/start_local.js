const { spawn, spawnSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const root = path.resolve(__dirname, '..');
const python = process.env.URBAN_PYTHON || (process.platform === 'win32' ? 'py' : 'python3');
const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';
const envFile = path.join(root, '.env');
const keyLine = fs.existsSync(envFile)
  ? fs.readFileSync(envFile, 'utf8').split(/\r?\n/).find(line => /^AMAP_API_KEY=/.test(line.trim()))
  : null;
const fileKey = keyLine ? keyLine.slice(keyLine.indexOf('=') + 1).trim().replace(/^['"]|['"]$/g, '') : '';
const apiEnv = { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8', AMAP_API_KEY: process.env.AMAP_API_KEY || fileKey };
const diagnostics = spawnSync(python, ['scripts/check_environment.py'], { cwd: root, stdio: 'inherit', windowsHide: true, env: apiEnv });
if (diagnostics.error || diagnostics.status !== 0) { console.error('启动检查失败，请先修复上方提示。Python可通过URBAN_PYTHON指定。'); process.exit(1); }
const api = spawn(python, ['-B', '-m', 'Back_end.local_api'], { cwd: root, stdio: 'inherit', env: apiEnv });
const web = spawn(npm, ['run', 'start:web'], { cwd: root, stdio: 'inherit', shell: process.platform === 'win32', env: { ...process.env, HOST: '127.0.0.1' } });

let stopping = false;
function stop() {
  stopping = true;
  if (process.platform === 'win32') { for (const child of [api, web]) if (child.pid) spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore', windowsHide: true }); }
  else { api.kill(); web.kill(); }
}
api.on('error', error => { console.error('本地数据服务启动失败:', error.message); stop(); });
web.on('error', error => { console.error('网页启动失败:', error.message); stop(); });
api.on('exit', code => { if (!stopping) { console.error('本地数据服务已退出:', code); process.exitCode = code || 1; web.kill(); } });
web.on('exit', code => { if (!stopping) { console.error('网页服务已退出:', code); process.exitCode = code || 1; api.kill(); } });
process.on('SIGINT', stop);
process.on('SIGTERM', stop);
