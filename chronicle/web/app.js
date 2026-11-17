const $ = (id) => document.getElementById(id);
const state = { projects: [], project: null, data: null, mode: 'timeline', date: '', month: '', sources: new Map(),
  conversations: [], conversation: null, sourceMessages: [], before: null, selected: new Map(),
  importProject: null, transcriptMessage: null, request: 0, sourceRequest: 0, loadingSource: false, desktop: false, runMode: "legacy", sourceAvailable: false, demo: false, preflight: null, savedProject: null, connectionPhase: "idle", loginDeadline: null };
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function button(text, className, action) {
  const node = el('button', className, text); node.type = 'button'; node.addEventListener('click', action); return node;
}
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || 'operation_failed');
  return value;
}
let toastTimer;
function toast(text) { $('toast').textContent = text; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 5000); }
function errorText(error) { return ({ selection_expired: '选择已过期，请重新打开会话。', select_messages_first: '请先勾选相关消息。', wechat_not_connected: '暂时无法读取本机微信。', developer_tools_required: '首次连接需要 Apple 命令行工具。', connection_candidate_expired: '连接列表已更新，请重新打开设置。', account_confirmation_required:'请先确认识别到的账号，或选择换个账号重试。', invalid_profile_label:'请填写不超过 100 字的账号备注。', keychain_permission_required: '请在系统提示中允许访问钥匙串，然后重试保存连接。' })[error.message] || '暂时无法完成操作，请重试。'; }
function empty(title, description, action, label) {
  const node = el('div', 'empty-state'); node.append(el('div', 'empty-symbol', '↳'), el('h3', '', title), el('p', '', description));
  if (action) node.append(button(label, 'button secondary', action));
  return node;
}
function formattedDate(value) { return new Date(value + 'T12:00:00').toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' }); }
function timeLabel(message) { return message.datetime.slice(11, 16); }
function sizeLabel(size) { return size ? size > 1048576 ? (size / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(size / 1024)) + ' KB' : ''; }
function kindLabel(kind) { return ({ text: '文字', image: '图片', voice: '语音', video: '视频', file: '文件', quote: '引用回复', other: '消息' })[kind] || '消息'; }
function link(url, text, className) { const node = el('a', className, text); node.href = url; node.target = '_blank'; node.rel = 'noopener'; return node; }

function showProjectDialog() { $('projectName').value = ''; $('projectDialog').showModal(); $('projectName').focus(); }
function resetFilters() { state.date = ''; state.sources.clear(); $('dateFilter').value = ''; $('search').value = ''; $('sourceFilter').value = ''; }
async function loadProjects(preferred) {
  const result = await api('/api/projects'); state.projects = result.projects.slice().sort((a,b) => b.created_at-a.created_at);
  const chosen = state.projects.find(p => p.id === (preferred || state.project?.id || state.savedProject)) || state.projects[0] || null;
  state.project = chosen; renderProjects();
  if (chosen) await loadTimeline(true);
  else {
    $('projectTitle').textContent = '开始一份新的档案'; $('projectSubtitle').textContent = '从一个项目开始，慢慢留下事情的来龙去脉。';
    $('timeline').replaceChildren(empty('为正在推进的事情建一份档案', '论文、合作或任何需要持续沟通的项目。', showProjectDialog, '新建第一个项目'));
    $('visibleCount').textContent = ''; $('calendar').replaceChildren();
  }
  $('openImport').disabled = $('addNote').disabled = !chosen; updateGuidance();
}
function renderProjects() {
  const nodes = state.projects.map(project => {
    const item = button('', 'project-item' + (state.project?.id === project.id ? ' active' : ''), async () => {
      if (state.project?.id === project.id) return;
      state.project = project; resetFilters();
      renderProjects(); await loadTimeline(true);
    });
    item.dataset.project = project.id;
    if (state.project?.id === project.id) item.setAttribute('aria-current', 'page');
    item.append(el('span', 'project-icon', '▤'), el('span', 'project-name', project.name), el('span', 'count', project.message_count)); return item;
  });
  $('projects').replaceChildren(...(nodes.length ? nodes : [el('div', 'empty-projects', '还没有项目，点击 ＋ 新建。')]));
}
async function loadTimeline(resetMonth = false) {
  if (!state.project) return;
  const request = ++state.request; const id = state.project.id;
  $('projectTitle').textContent = state.project.name;
  $('timeline').replaceChildren(el('div', 'loading', '正在打开档案…'));
  try {
    const data = await api('/api/timeline?project=' + encodeURIComponent(id) + (state.date ? '&date=' + state.date : ''));
    if (request !== state.request) return;
    state.data = data;
    $('projectSubtitle').textContent = data.total ? `已收录 ${data.total} 条沟通，来自 ${data.sources} 个会话。` : '把散落的对话与材料，放回事情发生的顺序。';
    $('statMessages').textContent = data.total; $('statSources').textContent = data.sources; $('statResources').textContent = data.resources;
    data.messages.forEach(m => state.sources.set(m.conversation, m.conversation));
    const oldSource = $('sourceFilter').value;
    $('sourceFilter').replaceChildren(new Option('全部会话', ''), ...[...state.sources].map(([value, name]) => new Option(name, value)));
    $('sourceFilter').value = state.sources.has(oldSource) ? oldSource : '';
    if (resetMonth || !state.month) state.month = (Object.keys(data.days).sort().at(-1) || new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Shanghai' })).slice(0, 7);
    renderCalendar(); renderTimeline(); updateGuidance(); savePreferences();
  } catch (error) { if (request === state.request) $('timeline').replaceChildren(empty('档案暂时没有打开', errorText(error), () => loadTimeline(), '重试')); }
}
function filteredMessages() {
  const query = $('search').value.trim().toLocaleLowerCase(), source = $('sourceFilter').value;
  return (state.data?.messages || []).filter(m => (!source || m.conversation === source) &&
    (!query || [m.text, m.title, m.sender, m.conversation, m.transcript?.text, m.quote?.sender, m.quote?.summary].join(' ').toLocaleLowerCase().includes(query)));
}
function renderTimeline() {
  const messages = filteredMessages(); $('visibleCount').textContent = messages.length + (state.mode === 'files' ? ' 条记录' : ' 条记录');
  $('allDates').classList.toggle('active', !state.date);
  $('timelineTab').setAttribute('aria-selected', String(state.mode === 'timeline')); $('filesTab').setAttribute('aria-selected', String(state.mode === 'files'));
  if (state.mode === 'files') return renderFiles(messages);
  if (!messages.length) return $('timeline').replaceChildren(empty(state.date ? '这一天还没有记录' : '还没有相关记录',
    $('search').value ? '试试其他关键词。' : '把与项目有关的沟通，归档到这里。', state.date ? clearDate : openImport, state.date ? '查看全部日期' : '归档沟通'));
  const nodes = []; let day = '';
  for (const message of messages) {
    const current = message.datetime.slice(0, 10);
    if (current !== day) {
      day = current; const heading = el('div', 'day-heading'); heading.append(el('span', '', formattedDate(day)), el('span', 'day-label',
        new Date(day + 'T12:00:00').toLocaleDateString('zh-CN', { weekday: 'long' }))); nodes.push(heading);
    }
    const entry = el('article', 'timeline-entry'); entry.dataset.message = message.id;
    entry.append(el('time', 'entry-time', timeLabel(message)), el('div', 'entry-spine'));
    const content = el('div', 'entry-content'), meta = el('div', 'entry-meta');
    meta.append(el('strong', '', message.sender), el('span', 'source-tag', message.conversation));
    if (message.source_conflict) meta.append(el('span', 'source-tag', '来源有变更'));
    content.append(meta);
    if (message.text) content.append(el('div', 'message-text', message.text));
    if (message.kind === 'quote') content.append(quoteNode(message.quote));
    else if (message.kind !== 'text') content.append(mediaNode(message, message.attachments));
    entry.append(content); nodes.push(entry);
  }
  $('timeline').replaceChildren(...nodes);
}
function fileNode(message, primary) {
  const name = message.title || primary?.name || kindLabel(message.kind) + (primary?.extension || '');
  const node = primary?.url ? link(primary.url, '', 'message-file') : el('div', 'message-file');
  node.append(el('span', 'file-icon', (primary?.extension?.slice(1) || 'FILE').toUpperCase().slice(0, 5)));
  const detail = el('div'); detail.append(el('div', 'file-name', name), el('div', 'file-detail', primary?.url ? [sizeLabel(primary.bytes), '已保存'].filter(Boolean).join(' · ') : '资源尚未补齐'));
  node.append(detail, el('span', 'file-arrow', primary?.url ? '↗' : '—')); return node;
}
function quoteNode(quote = {}) {
  const node = el('blockquote', 'quote-block');
  const header = el('div', 'quote-header');
  header.append(el('span', '', '↳ 引用 '), el('strong', '', quote.sender || '未知发言人'));
  node.append(header, el('div', 'quote-summary', quote.summary || '[引用消息]'));
  return node;
}
function mediaNode(message, assets = []) {
  const primary = assets.find(a => a.role === 'primary'), preview = assets.find(a => a.role === 'preview'), audio = assets.find(a => a.role === 'voice_playback');
  const wrap = el('div');
  if (message.kind === 'image') {
    const display = preview || (['.png', '.jpg', '.jpeg', '.webp', '.gif'].includes(primary?.extension?.toLowerCase()) ? primary : null);
    if (display?.url) {
      wrap.className = 'image-wrap'; const anchor = link(display.url, ''); const image = el('img', 'message-image');
      image.src = display.url; image.alt = '归档图片'; image.loading = 'lazy'; anchor.append(image); wrap.append(anchor);
      const caption = el('div', 'image-caption'); caption.append(el('span', '', primary?.thumbnail_only ? '当前为缩略图' : primary?.preview_first_frame_only ? '首帧预览' : preview ? '预览' : '图片'));
      if (primary?.url) caption.append(link(primary.url, '保存文件 ↗'));
      wrap.append(caption);
    } else wrap.append(fileNode(message, primary));
  } else if (message.kind === 'video' && primary?.url) {
    const video = el('video', 'message-video'); video.controls = true; video.preload = 'metadata'; video.src = primary.url;
    wrap.append(video, link(primary.url, '打开视频 ↗', 'text-button'));
  } else if (message.kind === 'voice') {
    wrap.className = 'voice-block'; const label = el('div', 'voice-label'); label.append(el('span', '', '◖ 语音' + (message.duration ? ` · ${message.duration} 秒` : '')));
    if (message.attachments?.length) label.append(button('补充文字', 'text-button', () => openTranscript(message)));
    wrap.append(label);
    if (audio?.url) { const player = el('audio'); player.controls = true; player.preload = 'none'; player.src = audio.url; wrap.append(player); }
    else if (primary?.url) wrap.append(link(primary.url, '下载语音原件 ↗', 'text-button'));
    const text = message.transcript?.text;
    wrap.append(el('div', 'voice-transcript' + (text ? '' : ' muted'), text || '尚未生成文字'));
  } else wrap.append(fileNode(message, primary));
  return wrap;
}
function renderFiles(messages) {
  const files = messages.filter(m => m.kind !== 'text' && m.kind !== 'quote' && m.attachments.length);
  $('visibleCount').textContent = files.length + ' 份资料';
  if (!files.length) return $('timeline').replaceChildren(empty('还没有相关附件', '归档时，文件和媒体会随消息一起保存。'));
  const grid = el('div', 'files-grid');
  for (const message of files) {
    const card = el('article', 'file-card'); card.append(mediaNode(message, message.attachments));
    const footer = el('div', 'card-footer'); footer.append(el('span', '', message.sender), el('span', '', message.datetime.slice(5, 10) + ' · ' + timeLabel(message)));
    card.append(footer); grid.append(card);
  }
  $('timeline').replaceChildren(grid);
}
function renderCalendar() {
  if (!state.month) return;
  const [year, month] = state.month.split('-').map(Number); $('monthLabel').textContent = year + ' 年 ' + month + ' 月';
  const nodes = [], leading = (new Date(year, month - 1, 1).getDay() + 6) % 7, count = new Date(year, month, 0).getDate();
  for (let i = 0; i < leading; i++) nodes.push(el('span', 'blank'));
  for (let day = 1; day <= count; day++) {
    const date = `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
    const has = state.data?.days[date]; const node = button(String(day), (has ? 'has-records' : '') + (state.date === date ? ' active' : ''), () => setDate(date));
    node.disabled = !has; node.setAttribute('aria-label', date + (has ? `，${has} 条记录` : '，无记录')); nodes.push(node);
  }
  $('calendar').replaceChildren(...nodes);
}
async function setDate(date) { state.date = date; $('dateFilter').value = date; await loadTimeline(); }
function clearDate() { return setDate(''); }
function moveMonth(direction) { const [year, month] = state.month.split('-').map(Number); const d = new Date(year, month - 1 + direction, 1); state.month = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0'); renderCalendar(); }

async function openImport() {
  if (!state.project) return;
  state.importProject = state.project; state.selected.clear(); state.conversation = null; state.sourceMessages = []; state.before = null;
  $('importTarget').textContent = '归入「' + state.project.name + '」'; $('conversationSearch').value = ''; updateSelection();
  $('sourceTitle').textContent = '选择一个会话'; $('sourceMessages').replaceChildren(empty('从左侧选择一个会话', '选择与项目有关的片段，支持跨会话归档。'));
  $('conversations').replaceChildren(el('div', 'loading', '正在读取会话…')); $('selectAll').disabled = true;
  $('importDialog').showModal();
  try { state.conversations = (await api('/api/conversations')).conversations; renderConversations(); }
  catch (error) { $('conversations').replaceChildren(empty('暂时无法读取微信', '已有档案仍可正常浏览。', async () => {
    $('importDialog').close();
    if (state.desktop) await openSettings();
    else await openImport();
  }, state.desktop ? '连接微信' : '重试读取')); }
}
function renderConversations() {
  const query = $('conversationSearch').value.trim().toLocaleLowerCase();
  const nodes = state.conversations.filter(c => [c.display_name, c.nickname, c.remark].join(' ').toLocaleLowerCase().includes(query)).map(c => {
    const node = button('', 'conversation-button' + (state.conversation?.conversation_id === c.conversation_id ? ' active' : ''), () => loadConversation(c));
    node.dataset.conversation = c.conversation_id;
    node.append(el('span', 'avatar', c.kind === 'group' ? '群' : c.display_name.slice(0, 1)));
    const text = el('span'); text.append(el('strong', '', c.display_name), el('small', '', c.kind === 'group' ? '群聊' : '私聊')); node.append(text); return node;
  });
  $('conversations').replaceChildren(...(nodes.length ? nodes : [el('div', 'error-inline', '没有找到相关会话。')]));
}
async function loadConversation(conversation, older = false) {
  if (older && state.loadingSource) return;
  const request = ++state.sourceRequest; state.loadingSource = true;
  if (!older) { state.conversation = conversation; state.sourceMessages = []; state.before = null; }
  const id = conversation.conversation_id;
  $('sourceTitle').textContent = conversation.display_name; $('sourceDescription').textContent = state.demo ? '示例会话 · 不读取微信' : '本机微信 · 只读'; renderConversations();
  if (!older) $('sourceMessages').replaceChildren(el('div', 'loading', '正在读取消息…'));
  try {
    const result = await api('/api/browse?conversation=' + encodeURIComponent(id) + (older && state.before ? '&before=' + encodeURIComponent(JSON.stringify(state.before)) : ''));
    if (state.conversation?.conversation_id !== id || request !== state.sourceRequest) return;
    const merged = [...result.messages.map(m => ({ ...m, token: result.token })), ...state.sourceMessages];
    state.sourceMessages = [...new Map(merged.map(m => [m.id, m])).values()]; state.before = result.before;
    renderSourceMessages(); $('selectAll').disabled = !state.sourceMessages.length;
  } catch (error) { if (request === state.sourceRequest) $('sourceMessages').replaceChildren(empty('消息暂时没有打开', errorText(error), () => loadConversation(conversation), '重试')); }
  finally { if (request === state.sourceRequest) state.loadingSource = false; }
}
function renderSourceMessages() {
  const nodes = [];
  if (state.before) nodes.push(button('↑ 加载更早的消息', 'source-load', () => loadConversation(state.conversation, true)));
  if (!state.sourceMessages.length) return $('sourceMessages').replaceChildren(empty('这个会话还没有本地记录', '可以换一个会话，或在微信里同步相关内容。'));
  for (const message of state.sourceMessages) {
    const row = el('article', 'source-row' + (state.selected.has(message.id) ? ' selected' : '')); row.dataset.sourceMessage = message.id;
    const checkbox = el('input'); checkbox.type = 'checkbox'; checkbox.id = 'pick-' + message.id; checkbox.checked = state.selected.has(message.id);
    checkbox.setAttribute('aria-label', `选择${message.sender}在${timeLabel(message)}的${kindLabel(message.kind)} ${message.title || message.text.slice(0, 24)}`);
    checkbox.addEventListener('change', () => { if (checkbox.checked) state.selected.set(message.id, { id: message.id, token: message.token, conversation: message.conversation }); else state.selected.delete(message.id); row.classList.toggle('selected', checkbox.checked); updateSelection(); });
    const content = el('div'), label = el('label'); label.htmlFor = checkbox.id;
    const meta = el('div', 'entry-meta'); meta.append(el('strong', '', message.sender), el('time', '', message.datetime.slice(5, 10) + ' ' + timeLabel(message)));
    const textual = message.kind === 'text' || message.kind === 'quote';
    label.append(meta, el('div', textual ? 'message-text' : 'source-type', textual ? message.text : kindLabel(message.kind) + (message.title ? ' · ' + message.title : '')));
    if (message.kind === 'quote') label.append(quoteNode(message.quote));
    content.append(label);
    if (['image', 'video', 'voice'].includes(message.kind)) {
      const preview = el('div', 'preview-slot');
      const action = button('预览' + kindLabel(message.kind), 'text-button', async () => {
        action.disabled = true; action.textContent = '正在读取…';
        try { const result = await api('/api/source-media?token=' + encodeURIComponent(message.token) + '&message=' + message.id);
          if (result.status !== 'copied') throw new Error('resource_missing'); preview.replaceChildren(mediaNode(message, result.assets));
        } catch { action.disabled = false; action.textContent = '重试预览'; toast('资源暂时不可用，可以先归档消息，之后补齐。'); }
      }); preview.append(action); content.append(preview);
    }
    row.append(checkbox, content); nodes.push(row);
  }
  $('sourceMessages').replaceChildren(...nodes);
}
function updateSelection() { const chats=new Set([...state.selected.values()].map(m=>m.conversation)).size; $('selectionCount').textContent = `已选 ${state.selected.size} 条${chats ? ' · '+chats+' 个会话' : ''}`; $('archiveSelected').disabled = state.selected.size === 0; if (!$('archiveSelected').dataset.busy) $('archiveSelected').textContent = state.selected.size ? `归档 ${state.selected.size} 条消息` : '勾选消息后归档'; }
function openNote() {
  const date = new Date(); const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  $('noteTime').value = local; $('noteText').value = ''; $('notePerson').value = '我'; $('noteFile').value = ''; $('noteDialog').showModal(); $('noteText').focus();
}
function openTranscript(message) { state.transcriptMessage = message.id; $('transcriptText').value = message.transcript?.text || ''; $('transcriptDialog').showModal(); }
function readFile(file) { return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result.split(',')[1]); reader.onerror = reject; reader.readAsDataURL(file); }); }

$('newProject').addEventListener('click', showProjectDialog);
document.querySelectorAll('[data-close]').forEach(node => node.addEventListener('click', () => $(node.dataset.close).close()));
$('importDialog').addEventListener('close', () => { state.sourceRequest++; state.loadingSource = false; });
$('projectForm').addEventListener('submit', async event => {
  event.preventDefault(); const submit = event.submitter; submit.disabled = true;
  try { const result = await api('/api/projects', { name: $('projectName').value }); $('projectDialog').close(); resetFilters(); await loadProjects(result.project_id); toast('项目已创建'); }
  catch (error) { toast(errorText(error)); } finally { submit.disabled = false; }
});
$('openImport').addEventListener('click', openImport); $('addNote').addEventListener('click', openNote);
$('timelineTab').addEventListener('click', () => { state.mode = 'timeline'; renderTimeline(); savePreferences(); });
$('filesTab').addEventListener('click', () => { state.mode = 'files'; renderTimeline(); savePreferences(); });
$('search').addEventListener('input', renderTimeline); $('sourceFilter').addEventListener('change', renderTimeline);
$('allDates').addEventListener('click', clearDate); $('dateFilter').addEventListener('change', event => setDate(event.target.value));
$('prevMonth').addEventListener('click', () => moveMonth(-1)); $('nextMonth').addEventListener('click', () => moveMonth(1));
$('conversationSearch').addEventListener('input', renderConversations);
$('selectAll').addEventListener('click', () => { const all = state.sourceMessages.every(m => state.selected.has(m.id)); state.sourceMessages.forEach(m => { if (all) state.selected.delete(m.id); else state.selected.set(m.id, { id: m.id, token: m.token, conversation: m.conversation }); }); renderSourceMessages(); updateSelection(); });
$('archiveSelected').addEventListener('click', async () => {
  const node = $('archiveSelected'); node.dataset.busy='true'; $('importDialog').querySelector('.import-body').inert=true; node.disabled = true; node.textContent = '正在保存消息和附件…';
  try { const result = await api('/api/archive', { project: state.importProject.id, selections: [...state.selected.values()] });
    $('importDialog').close(); resetFilters(); await loadProjects(state.importProject.id);
    toast(result.linked_to_project ? `已归档 ${result.linked_to_project} 条沟通${result.conflicts ? '，有来源变更待核对' : ''}` : '所选内容已在项目中，没有重复添加');
  } catch (error) { toast(errorText(error)); } finally { delete node.dataset.busy; $('importDialog').querySelector('.import-body').inert=false; updateSelection(); }
});
$('noteForm').addEventListener('submit', async event => {
  event.preventDefault(); const file = $('noteFile').files[0];
  if (!$('noteText').value.trim() && !file) return toast('请填写内容或添加一个文件。');
  if (file?.size > 12 * 1024 * 1024) return toast('附件超过 12 MB，请选择更小的文件。');
  const submit = event.submitter; submit.disabled = true;
  try { const data = { project: state.project.id, text: $('noteText').value, sender: $('notePerson').value, timestamp: Math.floor(new Date($('noteTime').value).getTime() / 1000) };
    if (file) data.file = { name: file.name, data: await readFile(file) };
    await api('/api/notes', data); $('noteDialog').close(); resetFilters(); await loadProjects(state.project.id); toast('记录已保存');
  } catch (error) { toast(errorText(error)); } finally { submit.disabled = false; }
});
$('transcriptForm').addEventListener('submit', async event => {
  event.preventDefault(); const submit = event.submitter; submit.disabled = true;
  try { await api('/api/transcript', { message: state.transcriptMessage, text: $('transcriptText').value }); $('transcriptDialog').close(); await loadTimeline(); toast('语音文字已保存'); }
  catch (error) { toast(errorText(error)); } finally { submit.disabled = false; }
});
function updateGuidance() {
  $('quickStart').hidden = state.demo || !!state.data?.total;
  $('stepProject').classList.toggle('done', !!state.project);
  $('stepConnect').classList.toggle('done', state.sourceAvailable);
  $('stepConnectTitle').textContent = state.sourceAvailable ? '选择沟通片段' : '添加沟通来源';
  $('stepConnectCopy').textContent = state.sourceAvailable ? '连接已就绪，可直接选择会话' : '复用已有连接，或完成首次连接';
  $('beginDesktop').textContent = state.project ? '进入当前项目 →' : '创建第一个项目 →';
}
let preferenceTimer;
function savePreferences() {
  if (state.runMode !== 'source' || state.demo || !state.project) return;
  clearTimeout(preferenceTimer);
  preferenceTimer=setTimeout(() => api('/api/setup/preferences',{project:state.project.id,date:state.date,mode:state.mode}).catch(()=>{}),300);
}
async function openSettings() {
  if (state.demo) return toast('这是独立示例，不会连接本机微信。');
  $('newConnectionPanel').hidden=true; $('settingsDialog').showModal(); await desktopState();
  if (connectionBusy(state.connectionPhase) || ['credentials_pending','confirm_account'].includes(state.connectionPhase)) return;
  if (state.runMode === 'source') await loadKnownConnections();
  const hasKnown=!!document.querySelector('.known-connection-card');
  $('newConnectionPanel').hidden=state.sourceAvailable || hasKnown;
  if (!state.sourceAvailable && !hasKnown) await checkConditions();
}
async function loadKnownConnections() {
  $('knownConnections').replaceChildren(el('div','loading','正在查找可复用的连接…'));
  try {
    const result=await api('/api/setup/connections');
    $('knownConnections').replaceChildren();
    if (result.connections.length) $('knownConnections').append(el('div','section-label','发现可复用的本机连接'));
    for (const candidate of result.connections) {
      const card=el('div','known-connection-card'), content=el('div');
      content.append(el('strong','',candidate.label),el('small','',candidate.conversation_count+' 个会话 · 无需重新登录'));
      const use=button(candidate.current?'保存到钥匙串':'使用此连接','button secondary',async()=>{
        use.disabled=true;use.textContent='正在验证…';
        try { await api('/api/setup/reuse',{candidate:candidate.id,label:candidate.label}); await desktopState(); $('newConnectionPanel').hidden=true;toast('已有连接已保存，无需重新初始化'); }
        catch(error){toast(errorText(error));use.disabled=false;use.textContent='重试保存';}
      });
      card.append(content,use);$('knownConnections').append(card);
    }
  } catch { $('knownConnections').replaceChildren(); }
}
function nativeAction(action) {
  if (window.webkit?.messageHandlers?.chronicleNative) return window.webkit.messageHandlers.chronicleNative.postMessage({action});
  if (action==='chooseConnection') return $('connectionFile').click();
  if (action==='installDeveloperTools') return api('/api/setup/tools',{}).then(()=>toast('请按 Apple 安装窗口的提示完成，随后重新检测。'));
  toast('源码运行时可用启动参数打开已有档案，详情见 README。');
}
let connectionTimer, countdownTimer;
function connectionBusy(phase) { return ['preparing','closing_wechat','waiting_for_login','verifying','restored','canceling'].includes(phase); }
function renderConnectionPhase(value) {
  const phase=value.connection.state, active=connectionBusy(phase), pending=['credentials_pending','confirm_account'].includes(phase);
  state.connectionPhase=phase;state.loginDeadline=value.connection.login_deadline;
  const stages={
    preparing:{step:1,title:'正在打开微信登录窗口',copy:'请稍候。准备好后，这里会提示你完成登录。',foot:'这一步由 Chronicle 自动完成。'},
    closing_wechat:{step:1,title:'请确认退出当前微信',copy:'如果微信弹出退出确认，请选择退出。随后会自动打开本次连接的登录窗口。',foot:'不会强制关闭微信；你可以在微信里取消退出。'},
    awaiting_quit:{step:1,title:'微信还没有退出',copy:'请先正常退出微信，再点击下方按钮继续。',foot:'准备好的档案不受影响。'},
    waiting_for_login:{step:2,title:'现在切到微信，确认登录',copy:'微信登录窗口已经打开。接下来需要你在微信和手机上完成登录。',instructions:['切到微信窗口，选择这次要连接的大号或小号。','点击登录；如果显示二维码或手机确认，请按微信提示完成。','登录成功后回到这里，确认识别出的昵称，并为账号加上备注。'],foot:'不需要再次点击“开始连接”。识别成功后，这里会显示账号昵称。'},
    verifying:{step:3,title:'登录已识别，正在验证连接',copy:'正在核对账号数据。完成后会自动恢复原版微信。',foot:'现在不需要操作，请保持页面打开。'},
    restored:{step:3,title:'正在识别账号身份',copy:'原版微信已恢复。识别出昵称后，请在这里确认账号并添加备注。',foot:'稍后会提示你确认实际登录的账号。'},
    canceling:{step:2,title:'正在取消并恢复微信',copy:'请稍候，结束本次连接后会恢复原版微信。',foot:'无需重复点击取消。'},
    cancelled:{step:1,title:'本次连接已取消',copy:'你可以重新检测条件，再次开始连接。',foot:'已有档案仍可正常查看。',tone:'warning'},
    confirm_account:{step:3,title:'已识别到微信账号，请确认',copy:'确认这是你准备归档的账号，再加上便于辨认的备注。以后会显示备注和昵称。',foot:'确认后才会保存连接；不会自动归档聊天内容。'},
    credentials_pending:{step:3,title:'登录已验证，还需允许保存连接',copy:'点击“允许保存连接”，并在系统钥匙串提示中允许访问。不需要重新登录微信。',foot:'凭据保存成功后，下一步是选择聊天和消息。',tone:'warning'},
    recovery_required:{step:1,title:'需要先恢复原版微信',copy:'请退出临时微信窗口，再从“应用程序”打开原版微信。恢复后再重新检测。',foot:'请先完成恢复，再开始新的连接。',tone:'warning'},
    failed:{step:1,title:'连接尚未完成',copy:value.connection.error==='login_not_verified'?'这次没有收到有效的登录结果。原版微信已恢复；准备好后可以重新连接。':'未能验证这次连接。请确认微信和手机上的登录步骤已完成，再重新连接。',foot:'当前没有保存成功的连接；已归档内容不受影响。',tone:'warning'}
  };
  const stage=stages[phase];
  $('settingsDialog').classList.toggle('is-connecting',active||pending||phase==='awaiting_quit');
  $('connectionJourney').hidden=!stage;
  $('connectionDialogTitle').textContent=active||pending?'完成微信连接':state.sourceAvailable?'微信连接已就绪':'连接微信';
  $('connectionDialogCopy').textContent=active||pending?'跟随下方当前步骤操作，页面会自动更新。':'首次连接完成后，就能在项目中选择聊天和消息。';
  if(stage) {
    $('connectionJourney').classList.toggle('warning',stage.tone==='warning');
    $('connectionStageIcon').textContent=stage.tone==='warning'?'!':stage.step;
    $('connectionStageLabel').textContent=phase==='waiting_for_login'||pending||phase==='awaiting_quit'?'现在需要你操作':active?'正在自动处理':'下一步';
    $('connectionStageTitle').textContent=stage.title;$('connectionStageCopy').textContent=stage.copy;
    $('connectionInstructions').replaceChildren(...(stage.instructions||[]).map(text=>el('li','',text)));
    $('connectionInstructions').hidden=!stage.instructions;
    $('connectionStageFootnote').textContent=stage.foot;
    document.querySelectorAll('[data-connection-step]').forEach(node=>{const step=Number(node.dataset.connectionStep);node.classList.toggle('current',step===stage.step);node.classList.toggle('complete',step<stage.step);node.setAttribute('aria-current',step===stage.step?'step':'false');});
  }
  $('confirmAccountPanel').hidden=phase!=='confirm_account';
  if(phase==='confirm_account') {
    const account=value.connection.account||{};
    $('confirmedAccountName').textContent=account.nickname||'已验证的微信账号（未读取到昵称）';
    $('confirmedAccountAvatar').textContent=account.nickname?.slice(0,1)||'微';
    $('confirmedAccountAlias').textContent=account.wechat_alias?'微信号：'+account.wechat_alias:'请结合刚才的微信登录窗口确认账号。';
    if(!$('confirmedAccountLabel').value)$('confirmedAccountLabel').value=value.connection.label||$('accountLabel').value||'';
  }
  $('connectionWindowHelp').hidden=phase!=='waiting_for_login';
  $('refreshConnection').hidden=phase!=='waiting_for_login';
  $('continueConnection').hidden=phase!=='awaiting_quit';
  $('cancelConnection').hidden=!active || ['closing_wechat','canceling'].includes(phase);
  $('retryCredentials').hidden=phase!=='credentials_pending';
  $('checkConnection').disabled=active||pending;
  $('detectedAccounts').disabled=active||pending||state.preflight?.developer_tools_ready===false;
  $('accountLabel').disabled=active||pending;
  const ready=state.preflight?.developer_tools_ready && state.preflight?.dependencies_ready!==false && state.preflight?.accounts.length && $('detectedAccounts').value;
  $('connectDesktop').disabled=active||pending||!ready;
  if(!active) $('connectDesktop').textContent=['failed','cancelled'].includes(phase)?'重新连接':state.preflight?.wechat_running?'退出微信并连接':'开始连接';
  if(phase==='connected') $('newConnectionPanel').hidden=true;
  clearInterval(countdownTimer);
  function countdown(){const remaining=Math.max(0,Math.ceil(state.loginDeadline-Date.now()/1000));$('connectionCountdown').hidden=phase!=='waiting_for_login'||!state.loginDeadline;$('connectionCountdown').textContent=remaining?`请在 ${Math.floor(remaining/60)}:${String(remaining%60).padStart(2,'0')} 内完成登录。超时后会结束本次连接，可重新尝试。`:'正在结束等待并检查连接结果，请稍候。';}
  countdown();if(phase==='waiting_for_login'&&state.loginDeadline)countdownTimer=setInterval(countdown,1000);
}

async function desktopState() {
  const value=await api('/api/desktop/state');
  const status=await api('/api/status');
  state.sourceAvailable=!!status.source_available;state.demo=!!status.demo;
  state.runMode=value.mode || 'native';
  $('desktopSettings').hidden=state.demo;
  $('demoBadge').hidden=$('demoNotice').hidden=!state.demo;
  $('tryDemo').hidden=state.demo || state.runMode!=='source';
  $('importArchive').hidden=value.native_file_picker!==undefined?!value.native_file_picker:state.runMode==='source';
  const placeholder=new Option('尚未连接','');placeholder.disabled=value.profiles.length>0 || !!value.legacy_available;
  const options=value.profiles.map(p=>new Option(p.label+(p.nickname&&p.nickname!==p.label?' · '+p.nickname:''),p.id));
  if(value.legacy_available)options.push(new Option('当前工作区的已验证连接','legacy'));
  $('desktopProfiles').replaceChildren(placeholder,...options);
  $('desktopProfiles').value=value.selected_profile || (value.legacy_available?'legacy':'');
  const selected=value.profiles.find(p=>p.id===value.selected_profile);
  $('profileRemarkEditor').hidden=!selected || state.runMode!=='source';
  if(selected && document.activeElement!==$('profileRemark'))$('profileRemark').value=selected.label;
  $('connection').textContent=state.demo?'示例工作区 · 不读取微信':state.sourceAvailable?'已连接 · '+(selected?.label||'现有工作区'):'微信尚未连接';
  document.querySelector('.connection-dot').classList.toggle('connected',state.sourceAvailable);
  $('connectionReady').hidden=!state.sourceAvailable || state.demo || connectionBusy(value.connection.state) || ['confirm_account','credentials_pending','awaiting_quit'].includes(value.connection.state);
  $('connectionReadyCopy').textContent=`${status.conversation_count ? '已找到 '+status.conversation_count+' 个会话。' : ''}下一步：选择聊天，勾选与项目有关的消息。`;
  $('backToProject').textContent=state.project?'选择聊天和消息 →':'创建项目 →';
  if(!value.welcome_complete && !$('welcomeDialog').open && !state.demo)$('welcomeDialog').showModal();
  if(value.welcome_complete && $('welcomeDialog').open)$('welcomeDialog').close();
  renderConnectionPhase(value);
  const phase=value.connection.state;
  clearTimeout(connectionTimer);
  if(connectionBusy(phase))connectionTimer=setTimeout(()=>desktopState().catch(()=>{}),1500);
  updateGuidance();
  return value;
}
async function checkConditions() {
  $('developerToolsStatus').textContent='正在检测本机条件…';$('connectDesktop').disabled=true;
  try {
    const info=await api('/api/desktop/preflight');state.preflight=info;
    const previous=$('detectedAccounts').value;
    const labels=info.accounts.map((a,index)=>new Option((a.label.startsWith('wxid_')?'尚未识别的本机账号 '+(index+1):a.label)+(a.nickname?' · '+a.nickname:'')+(a.directory_hint?'（目录尾号 '+a.directory_hint+'）':''),a.id));
    $('detectedAccounts').replaceChildren(new Option('登录微信后自动识别（推荐）','auto'),...labels);
    $('detectedAccounts').value=info.accounts.some(a=>a.id===previous)?previous:'auto';
    $('installDeveloperTools').hidden=info.developer_tools_ready;
    $('developerToolsStatus').textContent=!info.developer_tools_ready?'首次连接需要 Apple 命令行工具。准备好后重新检测即可。':info.dependencies_ready===false?'需要先在终端安装读取依赖：brew install sqlcipher zstd。完成后重新检测。':!info.accounts.length?'未找到本机账号，请确认已在这台 Mac 登录过微信并允许读取本地数据。':info.wechat_running?'条件已就绪。开始时会请求正常退出微信，并在完成后自动恢复。':'条件已就绪。下一步去微信登录你要连接的账号，不需要先辨认编号。';
    $('detectedAccounts').disabled=!info.developer_tools_ready;
    $('connectDesktop').textContent=info.wechat_running?'退出微信并连接':'开始连接';
    $('connectDesktop').disabled=!info.developer_tools_ready || info.dependencies_ready===false || !info.accounts.length || !$('detectedAccounts').value;
  } catch {$('developerToolsStatus').textContent='暂时无法读取本机条件，请检查微信数据访问权限。';}
}
$('welcomeDialog').addEventListener('close',()=>{if(state.desktop && !state.demo)api('/api/desktop/welcome',{}).catch(()=>{});});
$('openGuide').addEventListener('click',()=>{$('welcomeDialog').showModal();updateGuidance();});
$('stepProject').addEventListener('click',showProjectDialog);
$('stepConnect').addEventListener('click',()=>state.project&&state.sourceAvailable?openImport():state.desktop?openSettings():showProjectDialog());
$('stepArchive').addEventListener('click',()=>state.project?openImport():showProjectDialog());
$('desktopSettings').addEventListener('click',()=>openSettings().catch(error=>toast(errorText(error))));
$('beginDesktop').addEventListener('click',async()=>{if(state.desktop)await api('/api/desktop/welcome',{});$('welcomeDialog').close();if(!state.project)showProjectDialog();});
$('importArchive').addEventListener('click',()=>nativeAction('chooseArchive'));
$('importConnection').addEventListener('click',()=>nativeAction('chooseConnection'));
$('installDeveloperTools').addEventListener('click',()=>nativeAction('installDeveloperTools'));
$('showNewConnection').addEventListener('click',async()=>{$('newConnectionPanel').hidden=false;await checkConditions();});
$('backToProject').addEventListener('click',()=>{$('settingsDialog').close();if(state.project)openImport();else showProjectDialog();});
$('checkConnection').addEventListener('click',checkConditions);
$('desktopProfiles').addEventListener('change',async event=>{
  if(!event.target.value)return;
  try { await api(event.target.value==='legacy'?'/api/setup/legacy':'/api/desktop/profile',event.target.value==='legacy'?{}:{profile:event.target.value});await desktopState();await loadTimeline(); }
  catch(error){toast(errorText(error));await desktopState();}
});
$('connectionFile').addEventListener('change',async event=>{
  const file=event.target.files[0];if(!file)return;
  if(file.size>2*1024*1024)return toast('连接文件超过大小限制。');
  try {await api('/api/setup/import-connection',{manifest:JSON.parse(await file.text()),label:$('accountLabel').value.trim()||'本机微信'});await desktopState();toast('连接已保存到钥匙串');}
  catch(error){toast(errorText(error));}finally{event.target.value='';}
});
$('detectedAccounts').addEventListener('change',event=>{$('connectDesktop').disabled=!event.target.value||!state.preflight?.developer_tools_ready||state.preflight?.dependencies_ready===false||!state.preflight?.accounts.length;});
$('connectDesktop').addEventListener('click',async()=>{
  $('connectDesktop').disabled=true;$('confirmedAccountLabel').value='';
  try {const route=state.runMode==='source'?'/api/setup/connect':'/api/desktop/connect';await api(route,{account:$('detectedAccounts').value,label:$('accountLabel').value.trim()||'微信账号',quit_running:!!state.preflight?.wechat_running});await desktopState();}
  catch(error){$('connectionProgress').textContent=errorText(error);$('connectDesktop').disabled=false;}
});
$('confirmAccount').addEventListener('click',async()=>{const node=$('confirmAccount');node.disabled=true;try{await api('/api/setup/confirm-account',{label:$('confirmedAccountLabel').value});await desktopState();toast('账号已保存，接下来选择聊天和消息。');}catch(error){await desktopState();toast(errorText(error));}finally{node.disabled=false;}});
$('discardAccount').addEventListener('click',async()=>{try{await api('/api/setup/discard-account',{});await desktopState();$('newConnectionPanel').hidden=false;await checkConditions();toast('未保存这个账号。现在可重新连接你要使用的微信。');}catch(error){toast(errorText(error));}});
$('saveProfileRemark').addEventListener('click',async()=>{try{await api('/api/setup/profile-label',{profile:$('desktopProfiles').value,label:$('profileRemark').value});await desktopState();toast('账号备注已保存');}catch(error){toast(errorText(error));}});
$('refreshConnection').addEventListener('click',async()=>{const value=await desktopState();if(value.connection.state==='waiting_for_login')toast('尚未收到登录完成结果。请确认微信和手机上的登录步骤已完成，页面会自动更新。');});
$('continueConnection').addEventListener('click',async()=>{await checkConditions();if(!$('connectDesktop').disabled)$('connectDesktop').click();});
window.addEventListener('focus',()=>{if(connectionBusy(state.connectionPhase))desktopState().catch(()=>{});});
$('cancelConnection').addEventListener('click',async()=>{await api('/api/setup/cancel',{});await desktopState();});
$('retryCredentials').addEventListener('click',async()=>{try{await api('/api/desktop/retry-credentials',{});await desktopState();}catch(error){toast(errorText(error));}});
window.addEventListener('chronicle-native-updated',async()=>{await desktopState();await loadProjects();toast('导入完成');});
async function boot() {
 try {
  const status=await api('/api/status');state.sourceAvailable=!!status.source_available;state.desktop=!!status.setup_available||!!status.desktop;state.demo=!!status.demo;
  if(state.desktop){const setup=await api('/api/desktop/state');state.savedProject=setup.preferences?.project;state.runMode=setup.mode||'native';state.date=setup.preferences?.date||'';$('dateFilter').value=state.date;state.mode=setup.preferences?.mode==='files'?'files':'timeline';}
  await loadProjects();
  if(state.desktop)await desktopState();
  else{$('connection').textContent=status.source_available?'本机微信数据可用':'已有档案可离线浏览';document.querySelector('.connection-dot').classList.toggle('connected',status.source_available);}
  updateGuidance();
 }catch{$('timeline').replaceChildren(empty('暂时无法连接工作台','请确认本地工作台正在运行。',()=>location.reload(),'重新连接'));}
}
boot();
