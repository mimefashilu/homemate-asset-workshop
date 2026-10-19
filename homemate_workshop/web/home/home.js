let token = '';
const byId = id => document.getElementById(id);
async function api(path, data) {
  const response = await fetch(path, data ? {method:'POST', headers:{'Content-Type':'application/json','X-Studio-Token':token}, body:JSON.stringify(data)} : {});
  const result = await response.json();
  if (!response.ok || result.error) throw new Error(result.error || '操作失败');
  return result;
}
async function refresh() {
  const state = await api('/api/workshop'); token = state.token;
  byId('game-dir').textContent = state.game_dir; byId('workspace').textContent = state.workspace;
  byId('staged-count').textContent = state.staged_count; byId('model-count').textContent = state.model_count;
  byId('backup-count').textContent = state.backups.length;
  byId('environment').textContent = !state.game_available ? (state.portable ? '请在启动窗口选择游戏目录。' : '尚未找到游戏 data 目录，请在 config.local.json 中设置 game_dir 后重新启动。') : '本地环境已就绪，AHM 资源读写已内置。';
  byId('prepare-models').disabled = !state.extractor_available;
  const select = byId('backup-select'); select.replaceChildren();
  for (const backup of state.backups) select.add(new Option(`${backup.id} · ${backup.kind === 'models' ? '模型' : '贴图'} · ${backup.status}`, backup.id));
  if (!state.backups.length) select.add(new Option('暂无备份', ''));
  byId('restore-backup').disabled = !state.backups.length;
}
byId('prepare-models').onclick = async event => {
  const button = event.currentTarget; button.disabled = true;
  byId('prepare-status').textContent = '正在从当前游戏提取预览资源，首次处理需要一些时间…';
  try { const result = await api('/api/models/prepare', {}); byId('prepare-status').textContent = `已准备 ${result.models.length} 个模型，可以进入模型工坊。`; }
  catch (error) { byId('prepare-status').textContent = error.message; }
  finally { button.disabled = false; await refresh().catch(() => {}); }
};
byId('restore-backup').onclick = async event => {
  const identifier = byId('backup-select').value;
  if (!identifier || !confirm(`恢复备份 ${identifier}？请先完全退出游戏。`)) return;
  const button = event.currentTarget; button.disabled = true;
  try {
    const job = await api('/api/textures/job', {action:'restore', backup:identifier});
    while (true) {
      const state = await api('/api/textures/job/' + job.job);
      byId('restore-status').textContent = state.message;
      if (state.status === 'error') throw new Error(state.message);
      if (state.status === 'done') { byId('restore-status').textContent = '已恢复备份。模型预览可重新准备，以匹配当前游戏。'; break; }
      await new Promise(resolve => setTimeout(resolve, 500));
    }
  } catch (error) { byId('restore-status').textContent = error.message; }
  finally { button.disabled = false; await refresh().catch(() => {}); }
};
refresh().catch(error => { byId('environment').textContent = error.message; });
